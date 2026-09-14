import streamlit as st
import pandas as pd
import requests
import datetime
import time
import io
import base64
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

import config

st.set_page_config(page_title="Сборка WB + Ozon", page_icon="📦", layout="centered")

st.title("📦 Сборка заказов (2 WB + 1 Ozon)")
st.caption("Автоупаковка, деление мест 2+ шт, наклейки и лист подбора на почту")

# ----------------- ФУНКЦИИ WILDBERRIES -----------------
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
            return {"shop": shop_name, "error": f"Ошибка WB: {res.status_code}", "items": [], "pdf": None}
        orders = res.json().get("orders", [])
    except Exception as e:
        return {"shop": shop_name, "error": f"Сетевая ошибка WB: {e}", "items": [], "pdf": None}

    if not orders:
        return {"shop": shop_name, "orders_count": 0, "items": [], "pdf": None}

    order_ids = []
    pick_items = []
    for o in orders:
        order_ids.append(o["id"])
        pick_items.append({
            "Магазин": shop_name,
            "Отправление / Заказ": str(o.get("id")),
            "Артикул": o.get("article", "—"),
            "Кол-во": 1
        })

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
                pdf_bytes = base64.b64decode(file_b64)
    except Exception:
        pass

    return {
        "shop": shop_name,
        "orders_count": len(orders),
        "items": pick_items,
        "pdf": pdf_bytes
    }

# ----------------- ФУНКЦИИ OZON -----------------
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
            return {"shop": shop_name, "error": f"Ошибка Ozon: {res.status_code} ({res.text})", "items": [], "pdf": None}
        postings = res.json().get("result", {}).get("postings", [])
    except Exception as e:
        return {"shop": shop_name, "error": f"Сетевая ошибка Ozon: {e}", "items": [], "pdf": None}

    if not postings:
        return {"shop": shop_name, "orders_count": 0, "items": [], "pdf": None}

    pick_items = []
    packaged_numbers = []
    errors = []

    for p in postings:
        p_num = p.get("posting_number")
        packages = []

        for prod in p.get("products", []):
            art = prod.get("offer_id", "—")
            qty = prod.get("quantity", 1)
            sku = prod.get("sku")

            pick_items.append({
                "Магазин": shop_name,
                "Отправление / Заказ": p_num,
                "Артикул": art,
                "Кол-во": qty
            })

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
        if ship_res.status_code == 200:
            res_data = ship_res.json().get("result", [])
            if isinstance(res_data, list):
                packaged_numbers.extend(res_data)
            else:
                packaged_numbers.append(p_num)
        else:
            errors.append(f"{p_num}: {ship_res.text}")
            packaged_numbers.append(p_num)

    time.sleep(2)

    pdf_bytes = None
    if packaged_numbers:
        try:
            lbl_res = requests.post(
                "https://api-seller.ozon.ru/v2/posting/fbs/package-label",
                json={"posting_number": packaged_numbers},
                headers=headers
            )
            if lbl_res.status_code == 200:
                pdf_bytes = lbl_res.content
        except Exception:
            pass

    res_dict = {
        "shop": shop_name,
        "orders_count": len(postings),
        "packages_count": len(packaged_numbers),
        "items": pick_items,
        "pdf": pdf_bytes
    }
    if errors:
        res_dict["error"] = " | ".join(errors)
    return res_dict

# ----------------- ОТПРАВКА НА MAIL.RU -----------------
def send_email(subject, html_content, attachments):
    cfg = config.EMAIL_SETTINGS
    msg = MIMEMultipart()
    msg["From"] = cfg["sender_email"]
    msg["To"] = cfg["receiver_email"]
    msg["Subject"] = subject

    msg.attach(MIMEText(html_content, "html", "utf-8"))

    for filename, content in attachments:
        part = MIMEApplication(content, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(part)

    with smtplib.SMTP_SSL(cfg["smtp_server"], cfg["smtp_port"]) as server:
        server.login(cfg["sender_email"], cfg["app_password"])
        server.sendmail(cfg["sender_email"], cfg["receiver_email"], msg.as_string())

# ----------------- ИНТЕРФЕЙС -----------------
if st.button("🚀 Собрать все заказы (WB + Ozon)", type="primary", use_container_width=True):
    with st.spinner("Сборка заказов, разбиение мест и отправка на почту..."):
        all_results = []
        attachments = []
        all_pick_items = []

        # WB
        for acc in getattr(config, "WB_ACCOUNTS", []):
            res = process_wb(acc)
            all_results.append(res)
            all_pick_items.extend(res.get("items", []))
            if res.get("pdf"):
                clean_name = res['shop'].replace(' ', '_')
                attachments.append((f"Наклейки_{clean_name}.pdf", res["pdf"]))

        # Ozon
        for acc in getattr(config, "OZON_ACCOUNTS", []):
            res = process_ozon(acc)
            all_results.append(res)
            all_pick_items.extend(res.get("items", []))
            if res.get("pdf"):
                clean_name = res['shop'].replace(' ', '_')
                attachments.append((f"Наклейки_{clean_name}.pdf", res["pdf"]))

        st.session_state["results"] = all_results
        st.session_state["pick_items"] = all_pick_items

        if all_pick_items and hasattr(config, "EMAIL_SETTINGS"):
            now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")

            html_email = f"<h2>📋 Лист подбора заказов ({now_str})</h2>"
            html_email += "<table border='1' cellpadding='6' style='border-collapse: collapse; font-family: Arial;'>"
            html_email += "<tr style='background-color: #f2f2f2;'><th>Магазин</th><th>Номер отправления / заказа</th><th>Артикул</th><th>Кол-во</th></tr>"
            
            for it in all_pick_items:
                html_email += f"<tr><td>{it['Магазин']}</td><td>{it['Отправление / Заказ']}</td><td><b>{it['Артикул']}</b></td><td style='text-align:center;'>{it['Кол-во']}</td></tr>"
            html_email += "</table>"
            html_email += "<p>Наклейки прикреплены во вложении (PDF).</p>"

            df_pick = pd.DataFrame(all_pick_items)
            csv_buffer = io.StringIO()
            df_pick.to_csv(csv_buffer, index=False, sep=";", encoding="utf-8-sig")
            attachments.append((f"Лист_подбора_{now_str.replace(':', '_')}.csv", csv_buffer.getvalue().encode("utf-8-sig")))

            try:
                send_email(f"Лист подбора и наклейки WB/Ozon на {now_str}", html_email, attachments)
                st.success("✉️ Готово! Наклейки и лист подбора отправлены на mebel_2026@bk.ru!")
            except Exception as e:
                st.warning(f"Заказы собраны, но произошла ошибка отправки почты: {e}")
        else:
            st.info("Новых заказов для сборки нет.")

if "results" in st.session_state:
    st.subheader("Статус магазинов:")
    for res in st.session_state["results"]:
        st.markdown(f"**🏬 {res['shop']}**")
        if "error" in res and res["error"]:
            st.error(res["error"])
        else:
            cnt = res.get('orders_count', 0)
            st.write(f"Заказов в работе: **{cnt} шт.**")

if "pick_items" in st.session_state and st.session_state["pick_items"]:
    st.subheader("📋 Сводный лист подбора:")
    df_show = pd.DataFrame(st.session_state["pick_items"])
    st.dataframe(df_show, use_container_width=True, hide_index=True)
