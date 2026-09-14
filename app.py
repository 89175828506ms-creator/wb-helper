import streamlit as st
import pandas as pd
import requests
import datetime
import time
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

    # 1. Запрашиваем новые сборочные задания
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

    # 2. Подсчет артикулов и сбор ID заказов
    summary = defaultdict(int)
    order_ids = []
    for o in orders:
        art = o.get("article", "Без артикула")
        summary[art] += 1
        order_ids.append(o["id"])

    # 3. Создаем новую поставку
    now_str = datetime.datetime.now().strftime("%d.%m_%H:%M")
    supply_name = f"Сборка_{now_str}"
    create_url = "https://marketplace-api.wildberries.ru/api/v3/supplies"
    s_res = requests.post(create_url, json={"name": supply_name}, headers=headers).json()
    supply_id = s_res.get("id")

    if not supply_id:
        return {"shop": shop_name, "error": f"Не удалось создать поставку: {s_res}"}

    # 4. Добавляем заказы в поставку (актуальный метод WB API v3)
    add_url = f"https://marketplace-api.wildberries.ru/api/marketplace/v3/supplies/{supply_id}/orders"
    patch_orders = requests.patch(add_url, json={"orders": order_ids}, headers=headers)

    if patch_orders.status_code not in [200, 204]:
        return {
            "shop": shop_name,
            "supply_id": supply_id,
            "error": f"Ошибка привязки заказов к поставке: {patch_orders.status_code} ({patch_orders.text})",
            "orders_count": len(orders),
            "items": dict(summary)
        }

    # Даем WB 2 секунды зарегистрировать привязку
    time.sleep(2)

    # 5. Переводим поставку в доставку
    deliver_url = f"https://marketplace-api.wildberries.ru/api/v3/supplies/{supply_id}/deliver"
    deliver_res = requests.patch(deliver_url, headers=headers)

    if deliver_res.status_code in [200, 204]:
        status_msg = "✅ Заказы в сборке и переданы в доставку"
    else:
        status_msg = "📦 Заказы успешно привязаны к поставке (на сборке)"

    return {
        "shop": shop_name,
        "supply_id": supply_id,
        "supply_status": status_msg,
        "orders_count": len(orders),
        "total_items": len(orders),
        "items": dict(summary)
    }

if st.button("🚀 Собрать заказы (оба кабинета)", type="primary", use_container_width=True):
    with st.spinner("Формируем поставки и привязываем заказы..."):
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

        st.write(f"Заказов: **{res['orders_count']} шт.**")
        if res.get("supply_id"):
            st.caption(f"Поставка: `{res['supply_id']}` ➔ {res.get('supply_status', '')}")

        if res.get("items"):
            df = pd.DataFrame(
                [{"Артикул": k, "Количество (шт.)": v} for k, v in res["items"].items()]
            )
