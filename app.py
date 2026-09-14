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

st.set_page_config(page_title="Сборка Ozon", page_icon="📦", layout="centered")

st.title("📦 Сборка Ozon FBS")
st.caption("Автоупаковка, деление мест 2+ шт, нанесение артикула на наклейку и отправка на почту")

# Регистрация шрифта с поддержкой кириллицы
FONT_NAME = "Helvetica"
possible_fonts = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/Library/Fonts/Arial.ttf"
]
for f_path in possible_fonts:
    if os.path.exists(f_path):
        try:
            pdfmetrics.registerFont(TTFont("CustomBold", f_path))
            FONT_NAME = "CustomBold"
            break
        except Exception:
            pass

def add_article_to_pdf(original_pdf_bytes, article_text):
    """Накладывает нижнюю плашку с артикулом на каждую страницу PDF-наклейки."""
    try:
        reader = PdfReader(io.BytesIO(original_pdf_bytes))
        writer = PdfWriter()

        for page in reader.pages:
            w = float(page.mediabox.width)
            h = float(page.mediabox.height)

            # Создаем накладываемый слой
            packet = io.BytesIO()
            can = canvas.Canvas(packet, pagesize=(w, h))

            # Высота нижней плашки
            bar_height = max(24.0, h * 0.08)

            # Белый прямоугольник с черной рамкой
            can.setFillColorRGB(1, 1, 1)
            can.setStrokeColorRGB(0, 0, 0)
            can.setLineWidth(1)
            can.rect(2, 2, w - 4, bar_height, fill=1, stroke=1)

            # Текст артикула
            can.setFillColorRGB(0, 0, 0)
            font_size = min(14.0, bar_height * 0.5)
            can.setFont(FONT_NAME, font_size)

            display_text = f"АРТИКУЛ: {article_text}"
            # Если текст слишком длинный, немного уменьшаем размер
            text_width = can.stringWidth(display_text, FONT_NAME, font_size)
            if text_width > (w - 10):
                font_size = font_size * ((w - 10) / text_width)
                can.setFont(FONT_NAME, font_size)

            can.drawCentredString(w / 2.0, (bar_height - font_size) / 2.0 + 3, display_text)
            can.save()

            packet.seek(0)
            overlay_pdf = PdfReader(packet)
            page.merge_page(overlay_pdf.pages[0])
            writer.add_page(page)

        out_io = io.BytesIO()
        writer.write(out_io)
        return out_io.getvalue()
    except Exception as e:
        st.warning(f"Не удалось напечатать артикул на наклейке: {e}")
        return original_pdf_bytes

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

    packaged_groups = []  # [( [posting_numbers], article )]
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

            # 1 единица = 1 отдельное место
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

    # Скачивание наклеек и наложение артикула
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

def send_email(subject, filename, content):
    cfg = config.EMAIL_SETTINGS
    msg = MIMEMultipart()
    msg["From"] = cfg["sender_email"]
    msg["To"] = cfg["receiver_email"]
    msg["Subject"] = subject

    body = "<p>Во вложении этикетки Ozon с артикулами товаров для печати на термопринтере.</p>"
    msg.attach(MIMEText(body, "html", "utf-8"))

    part = MIMEApplication(content, Name=filename)
    part["Content-Disposition"] = f'attachment; filename="{filename}"'
    msg.attach(part)

    with smtplib.SMTP_SSL(cfg["smtp_server"], cfg["smtp_port"]) as server:
        server.login(cfg["sender_email"], cfg["app_password"])
        server.sendmail(cfg["sender_email"], cfg["receiver_email"], msg.as_string())

# Кнопка запуска
if st.button("🚀 Собрать Ozon и получить наклейки с артикулами", type="primary", use_container_width=True):
    with st.spinner("Собираем отправления, делим коробки, подписываем артикулы..."):
        all_results = []
        pdf_to_send = None

        for acc in getattr(config, "OZON_ACCOUNTS", []):
            res = process_ozon(acc)
            all_results.append(res)
            if res.get("pdf"):
                pdf_to_send = res["pdf"]

        st.session_state["results"] = all_results

        if pdf_to_send and hasattr(config, "EMAIL_SETTINGS"):
            now_str = datetime.datetime.now().strftime("%d.%m_%H_%M")
            try:
                send_email(
                    f"Наклейки Ozon с артикулами ({now_str})",
                    f"Наклейки_Ozon_{now_str}.pdf",
                    pdf_to_send
                )
                st.success("✉️ Готово! Этикетки с артикулами отправлены на mebel_2026@bk.ru!")
            except Exception as e:
                st.warning(f"Наклейки сформированы, но произошла ошибка почты: {e}")
        else:
            if not any(r.get("error") for r in all_results):
                st.info("Новых заказов Ozon, ожидающих сборки, нет.")

if "results" in st.session_state:
    st.subheader("Результат сборки:")
    for res in st.session_state["results"]:
        st.markdown(f"**🏬 {res['shop']}**")
        if "error" in res and res["error"]:
            st.error(res["error"])
        else:
            cnt = res.get('orders_count', 0)
            lbls = res.get('labels_count', 0)
            st.write(f"Заказов упаковано: **{cnt} шт.** | Наклеек сформировано: **{lbls} шт.**")