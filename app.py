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
