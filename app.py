import streamlit as st
import pandas as pd
import requests
import datetime
from collections import defaultdict

import config

st.set_page_config(page_title="Сборка WB", page_icon="🟣", layout="centered")

st.title("🟣 Сборка заказов WB")
st.caption("Управление 2 кабинетами Wildberries FBS")

def process_wb_shop(account):
    headers = {
        "Authorization": account["token"],
        "Content-Type": "application/json"
    }
    shop_name = account["name"]

    # 1. Получаем новые сборочные задания
    url_new = "https://marketplace-api.wildberries.ru/api/v3/orders/new"
    try:
        res = requests.get(url_new, headers=headers, timeout=15)
        if res.status_code != 200:
            return {"shop": shop_name, "error": f"Ошибка WB API ({res.status_code}): {res.text}"}
        orders = res.json().get("orders", [])
    except Exception as e:
        return {"shop": shop_name, "error": f"Сетевая ошибка: {e}"}

    if not orders:
        return {"shop": shop_name, "orders_count": 0, "total_items": 0, "items": {}}

    summary = defaultdict(int)
    order_ids = []
    for o in orders:
        art = o.get("article", "Без артикула")
        summary[art] += 1
        order_ids.append(o["id"])

    # 2. Создаем новую поставку
    now_str = datetime.datetime.now().strftime("%d.%m_%H:%M")
    supply_name = f"Сборка_{now_str}"
    create_url = "https://marketplace-api.wildberries.ru/api/v3/supplies"
    s_res = requests.post(create_url, json={"name": supply_name}, headers=headers).json()
    supply_id = s_res.get("id")

    if not supply_id:
        return {"shop": shop_name, "error": f"Не удалось создать поставку: {s_res}"}

    # 3. Добавляем заказы в поставку (пробуем PATCH, если нет — PUT)
    errors = []
    added = 0
    for oid in order_ids:
        add_url = f"https://marketplace-api.wildberries.ru/api/v3/supplies/{supply_id}/orders/{oid}"
        r = requests.patch(add_url, headers=headers)
        if r.status_code not in [200, 204]:
            r = requests.put(add_url, headers=headers)
        
        if r.status_code in [200, 204]:
            added += 1
        else:
            errors.append(f"Заказ {oid}: статус {r.status_code} ({r.text})")

    status_msg = f"Привязано {added} из {len(order_ids)} заказов"
    if errors:
        status_msg += f" | Ошибки: {'; '.join(errors[:2])}"

    return {
        "shop": shop_name,
        "supply_id": supply_id,
        "supply_status": status_msg,
        "orders_count": len(orders),
        "total_items": len(orders),
        "items": dict(summary)
    }

if st.button("🚀 Собрать заказы (оба кабинета)", type="primary", use_container_width=True):
    with st.spinner("Сборка и добавление в поставку..."):
        results = []
        for acc in config.WB_ACCOUNTS:
            res = process_wb_shop(acc)
            results.append(res)
        st.session_state["wb_results"] = results
        st.success("Готово!")

if "wb_results" in st.session_state:
    st.subheader("📋 Что взять со склада:")

    for res in st.session_state["wb_results"]:
        st.markdown(f"### 🏬 {res['shop']}")

        if "error" in res:
            st.error(res["error"])
            continue

        st.write(f"Заказов: **{res['orders_count']} шт.**")
        if res.get("supply_id"):
            st.caption(f"Поставка: `{res['supply_id']}` ➔ {res.get('supply_status')}")

        if res["items"]:
            df = pd.DataFrame(
                [{"Артикул": k, "Количество (шт.)": v} for k, v in res["items"].items()]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("Новых заказов нет.")
