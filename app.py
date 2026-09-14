import streamlit as st
import pandas as pd
import requests
import datetime
import time
import io
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import config

st.set_page_config(page_title="Сборка WB + Ozon", page_icon="📦", layout="centered")

st.title("📦 Сборка заказов (2 WB + 1 Ozon)")
st.caption("Автоупаковка, деление мест Ozon, артикулы на наклейках и отправка на почту")

# ----------------- ЗАГРУЗКА И РЕГИСТРАЦИЯ РУССКОГО ШРИФТА -----------------
FONT_NAME = "Helvetica"
FONT_PATH = "DejaVuSans-Bold.ttf"

if not os.path.exists(FONT_PATH):
    try:
        font_url = "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/resources/DejaVuSans-Bold.ttf"
        r = requests.get(font_url, timeout=15)
        if r.status_code == 200:
            with open(FONT_PATH, "wb") as f:
                f.write(r.content)
    except Exception:
        pass

if os.path.exists(FONT_PATH):
    try:
        pdfmetrics.registerFont(TTFont("DejaVuBold", FONT_PATH))
        FONT_NAME = "DejaVuBold"
    except Exception:
        pass


def add_article_to_pdf(original_pdf_bytes, article_text):
    """Накладывает плашку с артикулом в свободную верхнюю часть этикетки, не перекрывая QR-код."""
    try:
        reader = PdfReader(io.BytesIO(original_pdf_bytes))
        writer = PdfWriter()

        for page in reader.pages:
            w = float(page.mediabox.width)
            h = float(page.mediabox.height)

            packet = io.BytesIO()
            can = canvas.Canvas(packet, pagesize=(w, h))

            bar_height = 26.0
            # Размещаем плашку в верхнем свободном поле (h - bar_height - 6)
            y_pos = h - bar_height - 6.0

            # Белый прямоугольник с тонкой черной рамкой
            can.setFillColorRGB(1, 1, 1)
            can.setStrokeColorRGB(0, 0, 0)
            can.setLineWidth(1)
            can.rect(6, y_pos, w - 12, bar_height, fill=1, stroke=1)

            # Надпись артикула черным цветом
            can.setFillColorRGB(0, 0, 0)
            font_size = 12.0
            display_text = f"АРТИКУЛ: {article_text}"

            # Подгоняем размер шрифта, если артикул длинный
            text_width = can.stringWidth(display_text, FONT_NAME, font_size)
            if text_width > (w - 20):
                font_size = font_size * ((w - 20) / text_width)

            can.setFont(FONT_NAME, font_size)
            can.drawCentredString(w / 2.0, y_pos + (bar_height - font_size) / 2.0 + 2, display_text)
            can.save()

            packet.seek(0)
            overlay_pdf = PdfReader(packet)
            page.merge_page(overlay_pdf.pages[0])
            writer.add_page(page)

        out_io = io.BytesIO()
        writer.write(out_io)
        return out_io.getvalue()
    except Exception as e:
        st.warning(f"Предупреждение по наклейке: {e}")
        return original_pdf_bytes


# ----------------- WILDBERRIES -----------------
def process_wb(account):
    headers = {
        "Authorization": account["token"].strip(),
        "Content-Type": "application/json"
    }
    shop_name = account["name"]

    url_new = "https://marketplace-api.wildberries.ru/api/v3/orders/new"
    try:
        res = requests.get(url_new, headers=headers, timeout=15)
        if res.status_code != 200:
            return {"shop": shop_name, "error": f"Ошибка WB: {res.status_code}", "pdf": None}
        orders = res.json().get("orders", [])
    except Exception as e:
        return {"shop": shop_name, "error": f"Сетевая ошибка WB: {e}", "pdf": None}

    if not orders:
        return {"shop": shop_name, "orders_count": 0, "pdf": None}

    order_ids = [o["id"] for o in orders]

    now_str = datetime.datetime.now().strftime("%d.%m_%H:%M")
    s_res = requests.post(
        "https://marketplace-api.wildberries.ru/api/v3/supplies",
        json={"name": f"Сборка_{now_str}"},
        headers=headers
    ).json()
    supply_id = s_res.get("id")

    if supply_id:
        requests.patch(
            f"https://marketplace-api.wildberries.ru/api/marketplace/v3/supplies/{supply_id}/orders",
            json={"orders": order_ids},
            headers=headers
        )
        time.sleep(2)
        requests.patch(f"https://marketplace-api.wildberries.ru/api/v3/supplies/{supply_id}/deliver", headers=headers)

    pdf_bytes = None
    try:
        st_res = requests.post(
            "https://marketplace-api.wildberries.ru/api/v3/orders/stickers?type=pdf&width=58&height=40",
            json={"orders": order_ids},
            headers=headers
        )
        if st_res.status_code == 200:
            file_b64 = st_res.json().get("data", {}).get("file")
            if file_b64:
                import base64
                pdf_bytes = base64.b64decode(file_b64)
    except Exception:
        pass

    return {
        "shop": shop_name,
        "orders_count": len(orders),
        "pdf": pdf_bytes
    }


# ----------------- OZON -----------------
def process_ozon(account):
    headers = {
        "Client-Id": str(account["client_id"]).strip(),
        "Api-Key": str(account["api_key"]).strip(),
        "Content-Type": "application/json"
    }
    shop_name = account["name"]

    now = datetime.datetime.utcnow()
    since = (now - datetime.timedelta(days=14)).strftime("%Y-%m-%dT00:00:00Z")
    to = (now + datetime.timedelta(days=14)).strftime("%Y-%m-%dT23:59:59Z")

    list_url = "https://api-seller.ozon.ru/v3/posting/fbs/unfulfilled/list"
    payload = {
        "dir": "ASC",
        "filter": {
            "cutoff_from": since,
            "cutoff_to": to,
            "status": "awaiting_packaging"
        },
        "limit": 100,
        "with": {"analytics_data": False, "financial_data": False}
    }

    try:
        res = requests.post(list_url, json=payload, headers=headers, timeout=15)
        if res.status_code != 200:
            return {"shop": shop_name, "error": f"Ошибка Ozon: {res.status_code} ({res.text})", "pdf": None}
        postings = res.json().get("result", {}).get("postings", [])
    except Exception as e:
        return {"shop": shop_name, "error": f"Сетевая ошибка Ozon: {e}", "pdf": None}

    if not postings:
        return {"shop": shop_name, "orders_count": 0, "pdf": None}

    packaged_groups = []
    errors = []

    for p in postings:
        p_num = p.get("posting_number")
        packages = []
        arts = []

        for prod in p.get("products", []):
            art = prod.get("offer_id", "—")
            qty = prod.get("quantity", 1)
            sku = prod.get("sku")
            arts.append(art)

            for _ in range(qty):
                packages.append({
                    "products": [{
                        "product_id": sku,
                        "quantity": 1
                    }]
                })

        ship_res = requests.post(
            "https://api-seller.ozon.ru/v4/posting/fbs/ship",
            json={"packages": packages, "posting_number": p_num},
            headers=headers
        )

        article_label = ", ".join(arts)
        if ship_res.status_code == 200:
            res_data = ship_res.json().get("result", [])
            sub_postings = res_data if isinstance(res_data, list) else [p_num]
            packaged_groups.append((sub_postings, article_label))
        else:
            errors.append(f"{p_num}: {ship_res.text}")
            packaged_groups.append(([p_num], article_label))

    time.sleep(2)

    merged_writer = PdfWriter()
    total_labels = 0

    for post_nums, art_text in packaged_groups:
        try:
            lbl_res = requests.post(
                "https://api-seller.ozon.ru/v2/posting/fbs/package-label",
                json={"posting_number": post_nums},
                headers=headers
            )
            if lbl_res.status_code == 200:
                annotated_pdf = add_article_to_pdf(lbl_res.content, art_text)
                part_reader = PdfReader(io.BytesIO(annotated_pdf))
                for page in part_reader.pages:
                    merged_writer.add_page(page)
                    total_labels += 1
        except Exception as e:
            errors.append(f"Ошибка наклейки {post_nums}: {e}")

    final_pdf_bytes = None
    if total_labels > 0:
        out_buf = io.BytesIO()
        merged_writer.write(out_buf)
        final_pdf_bytes = out_buf.getvalue()

    res_dict = {
        "shop": shop_name,
        "orders_count": len(postings),
        "labels_count": total_labels,
        "pdf": final_pdf_bytes
    }
    if errors:
        res_dict["error"] = " | ".join(errors)
    return res_dict


# ----------------- ОТПРАВКА НА ПОЧТУ -----------------
def send_email(subject, attachments):
    cfg = config.EMAIL_SETTINGS
    msg = MIMEMultipart()
    msg["From"] = cfg["sender_email"]
    msg["To"] = cfg["receiver_email"]
    msg["Subject"] = subject

    body = "<p>Во вложении файлы этикеток для сборки заказов (WB и Ozon).</p>"
    msg.attach(MIMEText(body, "html", "utf-8"))

    for filename, content in attachments:
        part = MIMEApplication(content, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(part)

    with smtplib.SMTP_SSL(cfg["smtp_server"], cfg["smtp_port"]) as server:
        server.login(cfg["sender_email"], cfg["app_password"])
        server.sendmail(cfg["sender_email"], cfg["receiver_email"], msg.as_string())


# ----------------- ИНТЕРФЕЙС -----------------
if st.button("🚀 Собрать все заказы (WB + Ozon)", type="primary", use_container_width=True):
    with st.spinner("Сборка заказов, деление мест Ozon и подготовка наклеек..."):
        all_results = []
        attachments = []

        # 1. Сборка Wildberries
        for acc in getattr(config, "WB_ACCOUNTS", []):
            res = process_wb(acc)
            all_results.append(res)
            if res.get("pdf"):
                clean_name = res["shop"].replace(" ", "_")
                attachments.append((f"Наклейки_{clean_name}.pdf", res["pdf"]))

        # 2. Сборка Ozon (с артикулами на наклейках)
        for acc in getattr(config, "OZON_ACCOUNTS", []):
            res = process_ozon(acc)
            all_results.append(res)
            if res.get("pdf"):
                clean_name = res["shop"].replace(" ", "_")
                attachments.append((f"Наклейки_{clean_name}_с_артикулами.pdf", res["pdf"]))

        st.session_state["results"] = all_results

        # 3. Отправка одним письмом на почту
        if attachments and hasattr(config, "EMAIL_SETTINGS"):
            now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
            try:
                send_email(f"Наклейки WB + Ozon на {now_str}", attachments)
                st.success("✉️ Готово! Все наклейки (WB + Ozon с артикулами) отправлены на mebel_2026@bk.ru!")
            except Exception as e:
                st.warning(f"Заказы собраны, но возникла ошибка почты: {e}")
        else:
            if not any(r.get("error") for r in all_results):
                st.info("Новых заказов для сборки нет.")

if "results" in st.session_state:
    st.subheader("Статус сборки:")
    for res in st.session_state["results"]:
        st.markdown(f"**🏬 {res['shop']}**")
        if "error" in res and res["error"]:
            st.error(res["error"])
        else:
            cnt = res.get("orders_count", 0)
            st.write(f"Заказов в работе: **{cnt} шт.**")