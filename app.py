import streamlit as st
from google import genai
from PIL import Image
import os
import json
import pandas as pd
from supabase import create_client, Client
import streamlit.components.v1 as components

# --- SUPABASE & GEMINI CONFIG ---
SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.environ.get("SUPABASE_URL"))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", os.environ.get("SUPABASE_KEY"))
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

gemini_api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY"))
client = genai.Client(api_key=gemini_api_key)

# --- VERSIONSHANTERING ---
VERSION = "v1.16"
st.title(f"📖 Receptboken med AI-skanning ({VERSION})")

# --- INLOGGNINGSHANTERING ---
if "user" not in st.session_state:
    st.session_state["user"] = None

if not st.session_state["user"]:
    st.subheader("🔑 Logga in")
    
    with st.form("login_form"):
        email = st.text_input("E-post", key="login_email", autocomplete="email")
        password = st.text_input("Lösenord", type="password", key="login_pass", autocomplete="current-password")
        
        col1, col2 = st.columns(2)
        with col1:
            submit_login = st.form_submit_button("Logga in")
        with col2:
            submit_signup = st.form_submit_button("Skapa konto")
            
        if submit_login:
            try:
                auth_res = supabase.auth.sign_in_with_password({"email": email, "password": password})
                st.session_state["user"] = auth_res.user
                st.success("Inloggad!")
                st.rerun()
            except Exception as e:
                st.error(f"Fel vid inloggning: {e}")
                
        if submit_signup:
            try:
                auth_res = supabase.auth.sign_up({"email": email, "password": password})
                st.info("Konto skapat! Om e-postbekräftelse krävs, kolla din inkorg.")
            except Exception as e:
                st.error(f"Kunde inte skapa konto: {e}")
                
    st.stop()

# Status för inloggad användare
col_user, col_logout = st.columns([3, 1])
with col_user:
    st.write(f"Inloggad som: **{st.session_state['user'].email}**")
with col_logout:
    if st.button("Logga ut"):
        supabase.auth.sign_out()
        st.session_state["user"] = None
        st.rerun()

st.divider()

if "uploader_key" not in st.session_state:
    st.session_state["uploader_key"] = 0

# --- HUVUDAPP (FLIKAR) ---
flik1, flik2, flik3 = st.tabs(["📷 Skanna Recept", "📚 Mina Recept", "👥 Delade Böcker"])

with flik1:
    st.header("Skanna handskrivet recept")
    
    uppuploadad_bild = st.file_uploader(
        "Välj bild på recept...", 
        type=["jpg", "jpeg", "png"], 
        key=f"uploader_{st.session_state['uploader_key']}"
    )
    
    if uppuploadad_bild:
        bild = Image.open(uppuploadad_bild)
        bild.thumbnail((1000, 1000))
        st.image(bild, caption="Uppladdad bild (optimerad)", width=300)
        
        if st.button("Analysera recept med AI", key="btn_analysera"):
            with st.spinner("Analyserar handstil och beräknar näringsvärden..."):
                try:
                    prompt = (
                        "Analysera denna bild av ett recept. "
                        "Returnera svaret strikt som en JSON-struktur med följande nycklar:\n"
                        "- 'titel': Receptets namn\n"
                        "- 'kategori': Passande kategori (t.ex. Huvudrätt, Efterrätt, Bakverk)\n"
                        "- 'text': Renskriven version av receptet med ingredienser och instruktioner. "
                        "Använd tydliga rubriker som '### 🛒 Ingredienser' och '### 👨‍🍳 Instruktioner'.\n"
                        "- 'naring': Ett objekt med uppskattade näringsvärden uppdelade i 'per_portion' och 'per_100g'. "
                        "Inkludera nycklarna: 'energi' (kcal/kJ), 'protein' (g), 'kolhydrater' (g), 'socker' (g), "
                        "'fett' (g), 'mattat_fett' (g), 'fiber' (g), 'salt' (g).\n"
                        "Svara ENDAST med giltig JSON utan markdown-kodblock."
                    )
                    
                    response = client.models.generate_content(
                        model='gemini-3.8-flash',
                        contents=[bild, prompt]
                    )
                    
                    raw_text = response.text.strip()
                    if raw_text.startswith("```json"):
                        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                    
                    recept_data = json.loads(raw_text)
                    st.session_state["analyserat_recept"] = recept_data
                    st.success("Receptet har analyserats!")
                    
                except Exception as e:
                    st.error(f"Ett fel uppstod vid anropet till Gemini: {e}")

        if "analyserat_recept" in st.session_state:
            rec = st.session_state["analyserat_recept"]
            
            st.subheader("Granska & Spara")
            
            col_titel, col_kat = st.columns([2, 1])
            with col_titel:
                titel_input = st.text_input("Titel", value=rec.get("titel", ""))
            with col_kat:
                kategori_input = st.text_input("Kategori", value=rec.get("kategori", "Övrigt"))
            
            st.markdown("### 📊 Näringsinnehåll")
            
            naring_data = rec.get("naring", {})
            p_data = naring_data.get("per_portion", {})
            h_data = naring_data.get("per_100g", {})
            
            df_naring = pd.DataFrame({
                "Näringsämne (Makros)": [
                    "⚡ Energi", "🥩 Protein", "🍞 Kolhydrater", 
                    "varav sockerarter", "🥑 Fett", "varav mättat fett", 
                    "🌾 Fiber", "🧂 Salt"
                ],
                "Per 100 g": [
                    h_data.get("energi", "-"), h_data.get("protein", "-"), 
                    h_data.get("kolhydrater", "-"), h_data.get("socker", "-"), 
                    h_data.get("fett", "-"), h_data.get("mattat_fett", "-"), 
                    h_data.get("fiber", "-"), h_data.get("salt", "-")
                ],
                "Per Portion": [
                    p_data.get("energi", "-"), p_data.get("protein", "-"), 
                    p_data.get("kolhydrater", "-"), p_data.get("socker", "-"), 
                    p_data.get("fett", "-"), p_data.get("mattat_fett", "-"), 
                    p_data.get("fiber", "-"), p_data.get("salt", "-")
                ]
            })
            
            st.table(df_naring)
            
            text_input = st.text_area("Recepttext (Ingredienser & Instruktioner)", value=rec.get("text", ""), height=220)
            
            if st.button("💾 Spara till databasen", key="btn_spara"):
                naring_table_md = (
                    "\n\n### 📊 Näringsinnehåll\n"
                    "| Näringsämne (Makros) | Per 100 g | Per Portion |\n"
                    "| :--- | :---: | :---: |\n"
                    f"| ⚡ Energi | {h_data.get('energi', '-')} | {p_data.get('energi', '-')} |\n"
                    f"| 🥩 Protein | {h_data.get('protein', '-')} | {p_data.get('protein', '-')} |\n"
                    f"| 🍞 Kolhydrater | {h_data.get('kolhydrater', '-')} | {p_data.get('kolhydrater', '-')} |\n"
                    f"| varav sockerarter | {h_data.get('socker', '-')} | {p_data.get('socker', '-')} |\n"
                    f"| 🥑 Fett | {h_data.get('fett', '-')} | {p_data.get('fett', '-')} |\n"
                    f"| varav mättat fett | {h_data.get('mattat_fett', '-')} | {p_data.get('mattat_fett', '-')} |\n"
                    f"| 🌾 Fiber | {h_data.get('fiber', '-')} | {p_data.get('fiber', '-')} |\n"
                    f"| 🧂 Salt | {h_data.get('salt', '-')} | {p_data.get('salt', '-')} |\n"
                )
                
                full_text = f"{text_input}{naring_table_md}"
                
                data_att_spara = {
                    "user_id": st.session_state["user"].id,
                    "titel": titel_input,
                    "kategori": kategori_input,
                    "text": full_text
                }
                
                try:
                    supabase.table("recept").insert(data_att_spara).execute()
                    st.success("Receptet har sparats!")
                    
                    del st.session_state["analyserat_recept"]
                    st.session_state["uploader_key"] += 1
                    
                    st.rerun()
                except Exception as e:
                    st.error(f"Kunde inte spara till databasen: {e}")

with flik2:
    st.header("Mina sparade recept")
    
    with st.expander("⚙ Inställningar för delning"):
        nuvarande_status = False
        try:
            user_check = supabase.table("recept").select("is_public").eq("user_id", st.session_state["user"].id).limit(1).execute()
            if user_check.data and len(user_check.data) > 0:
                nuvarande_status = bool(user_check.data[0].get("is_public", False))
        except Exception as e:
            st.error(f"Kunde inte läsa delningsstatus: {e}")

        gör_publik = st.checkbox("🌍 Gör hela min receptbok publik (synlig under Delade böcker)", value=nuvarande_status)
        
        if st.button("Uppdatera delningsstatus"):
            try:
                supabase.table("recept").update({"is_public": gör_publik}).eq("user_id", st.session_state["user"].id).execute()
                st.success("Delningsinställningen har sparats!")
                st.rerun()
            except Exception as e:
                st.error(f"Kunde inte uppdatera delningsstatus: {e}")

    sokord = st.text_input("🔍 Sök i dina recept...", "", key="sok_recept")
    
    try:
        res = supabase.table("recept").select("*").eq("user_id", st.session_state["user"].id).execute()
        recept_lista = res.data
        
        if not recept_lista:
            st.info("Inga sparade recept hittades.")
        else:
            for r in recept_lista:
                titel = r.get("titel") or "Namnlöst recept"
                kategori = r.get("kategori") or "Övrigt"
                bild_url = r.get("bild_url") or r.get("image_url")
                recept_id = r.get('id')
                
                raw_db_text = r.get("text", "")
                
                clean_text = (raw_db_text
                              .replace("&nbsp;", " ")
                              .replace("Â", "")
                              .replace("â", ""))
                
                if "### 📊 Näringsinnehåll" in clean_text:
                    delar = clean_text.split("### 📊 Näringsinnehåll")
                    recept_text_clean = delar[0].strip()
                    narings_del = delar[1].strip()
                else:
                    recept_text_clean = clean_text.strip()
                    narings_del = ""
                
                if sokord.lower() in str(titel).lower() or sokord.lower() in str(recept_text_clean).lower():
                    with st.expander(f"📌 {titel} ({kategori})"):
                        
                        edit_key = f"edit_mode_{recept_id}"
                        if edit_key not in st.session_state:
                            st.session_state[edit_key] = False
                            
                        col_visa1, col_visa2 = st.columns([10, 2])
                        with col_visa2:
                            if st.button("✏️ Redigera", key=f"btn_toggle_edit_{recept_id}"):
                                st.session_state[edit_key] = not st.session_state[edit_key]
                                st.rerun()

                        if st.session_state[edit_key]:
                            # --- REDIGERINGSVY ---
                            ny_titel = st.text_input("Titel", value=titel, key=f"ny_titel_{recept_id}")
                            ny_kategori = st.text_input("Kategori", value=kategori, key=f"ny_kat_{recept_id}")
                            ny_text = st.text_area("Recepttext & Näringsinnehåll", value=raw_db_text, height=300, key=f"ny_text_{recept_id}")
                            
                            col_spara, col_avbryt = st.columns(2)
                            with col_spara:
                                if st.button("💾 Spara ändringar", key=f"save_{recept_id}"):
                                    try:
                                        supabase.table("recept").update({
                                            "titel": ny_titel,
                                            "kategori": ny_kategori,
                                            "text": ny_text
                                        }).eq("id", recept_id).execute()
                                        st.success("Ändringarna har sparats!")
                                        st.session_state[edit_key] = False
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Kunde inte uppdatera: {e}")
                            with col_avbryt:
                                if st.button("Avbryt", key=f"cancel_{recept_id}"):
                                    st.session_state[edit_key] = False
                                    st.rerun()
                        else:
                            # --- VANLIG VISNINGSVY ---
                            if bild_url:
                                st.image(bild_url, width=250)
                                
                            st.markdown(recept_text_clean, unsafe_allow_html=True)
                            if narings_del:
                                st.markdown("### 📊 Näringsinnehåll\n" + narings_del)
                            
                            st.divider()
                            
                            full_visnings_text = f"{recept_text_clean}\n\n### 📊 Näringsinnehåll\n{narings_del}" if narings_del else recept_text_clean
                            recept_innehall = f"Recept: {titel} ({kategori})\n\n{full_visnings_text}"
                            safe_text = json.dumps(recept_innehall)
                            
                            print_html_content = recept_text_clean.replace(chr(92), chr(92)*2).replace("'", "\\'").replace(chr(10), "<br>")
                            
                            narings_html_print = ""
                            if narings_del:
                                naring_lines = narings_del.split("\n")
                                table_rows = []
                                for line in naring_lines:
                                    if "|" in line and "---" not in line:
                                        cols = [c.strip() for c in line.split("|") if c.strip() != ""]
                                        if cols:
                                            table_rows.append(cols)
                                if table_rows:
                                    narings_html_print += "<h3>📊 Näringsinnehåll</h3><table>"
                                    for i, row in enumerate(table_rows):
                                        tag = "th" if i == 0 else "td"
                                        narings_html_print += "<tr>"
                                        for col in row:
                                            narings_html_print += f"<{tag}>{col}</{tag}>"
                                        narings_html_print += "</tr>"
                                    narings_html_print += "</table>"
                            
                            print_html = f"""
                            <!DOCTYPE html>
                            <html>
                            <head>
                            <meta charset="utf-8">
                            <style>
                              body {{ margin: 0; font-family: sans-serif; background-color: transparent; }}
                              .btn-container {{ display: flex; gap: 8px; flex-wrap: wrap; }}
                              button {{
                                  background-color: #ff4b4b;
                                  color: white;
                                  border: none;
                                  padding: 8px 14px;
                                  border-radius: 6px;
                                  font-weight: 600;
                                  cursor: pointer;
                                  font-size: 13px;
                                  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                              }}
                              button:hover {{ background-color: #ff2b2b; }}
                              #status {{ font-size: 13px; color: #666; align-self: center; }}
                            </style>
                            </head>
                            <body>
                                <div class="btn-container">
                                    <button onclick="printRecipe()">🖨 Skriv ut recept</button>
                                    <button onclick="copyRecipe()">📋 Kopiera text</button>
                                    <span id="status"></span>
                                </div>

                                <script>
                                function printRecipe() {{
                                    const win = window.open('', '_blank');
                                    if (!win) {{
                                        alert('Tillåt popups i webbläsaren för att skriva ut.');
                                        return;
                                    }}
                                    
                                    let content = `{print_html_content}`;
                                    // Byt ut ### mot snygga HTML-rubriker i utskriften
                                    content = content.replace(/###\s*(.*?)(<br>|$)/g, '<h3 style="margin-top:20px; font-size:16px; font-weight:bold;">$1</h3>');

                                    win.document.write('<!DOCTYPE html><html><head><meta charset="utf-8"><title>{titel}</title><style>body {{ font-family: sans-serif; padding: 30px; color: #111; line-height: 1.5; }} h2 {{ margin-bottom: 5px; }} p {{ color: #555; }} hr {{ border: 0; border-top: 1px solid #ddd; margin: 20px 0; }} table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }} th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }} th {{ background-color: #f4f4f4; }}</style></head><body>');
                                    win.document.write('<h2>{titel}</h2>');
                                    win.document.write('<p><em>Kategori: {kategori}</em></p>');
                                    win.document.write('<hr/>');
                                    win.document.write('<div>' + content + '</div>');
                                    win.document.write('{narings_html_print}');
                                    win.document.write('</body></html>');
                                    win.document.close();
                                    win.focus();
                                    
                                    setTimeout(() => {{
                                        win.print();
                                        win.close();
                                    }}, 500);
                                }}

                                function copyRecipe() {{
                                    const textToShare = {safe_text};
                                    const statusEl = document.getElementById('status');
                                    navigator.clipboard.writeText(textToShare).then(() => {{
                                        statusEl.innerText = 'Kopierat!';
                                        setTimeout(() => {{ statusEl.innerText = ''; }}, 2000);
                                    }});
                                }}
                                </script>
                            </body>
                            </html>
                            """
                            components.html(print_html, height=45)
                            
                            if st.button("🗑️ Radera recept", key=f"del_{recept_id}"):
                                supabase.table("recept").delete().eq("id", recept_id).execute()
                                st.success("Receptet raderades!")
                                st.rerun()
                            
    except Exception as e:
        st.error(f"Kunde inte hämta recept från databasen: {e}")

with flik3:
    st.header("👥 Delade receptböcker")
    st.markdown("Här kan du välja och läsa andra användares publika receptböcker via rullgardinsmenyn nedan.")
    
    try:
        publika_res = supabase.table("recept").select("*").eq("is_public", True).execute()
        publika_recept = publika_res.data
        
        if not publika_recept:
            st.info("Inga publika receptböcker hittades just nu.")
        else:
            användare_dict = {}
            for rec in publika_recept:
                uid = rec.get("user_id")
                if uid not in användare_dict:
                    användare_dict[uid] = []
                användare_dict[uid].append(rec)
            
            vald_användare = st.selectbox(
                "Välj receptbok att kika i:", 
                options=list(användare_dict.keys()),
                format_func=lambda x: f"Receptbok av användare ({x[:8]}...)"
            )
            
            if vald_användare:
                st.divider()
                st.subheader("📖 Receptsamling")
                
                for r in användare_dict[vald_användare]:
                    titel = r.get("titel") or "Namnlöst recept"
                    kategori = r.get("kategori") or "Övrigt"
                    recept_id = r.get('id')
                    
                    raw_db_text_d = r.get("text", "")
                    clean_text_d = (raw_db_text_d
                                    .replace("&nbsp;", " ")
                                    .replace("Â", "")
                                    .replace("â", ""))
                    
                    if "### 📊 Näringsinnehåll" in clean_text_d:
                        delar_d = clean_text_d.split("### 📊 Näringsinnehåll")
                        recept_text_clean_d = delar_d[0].strip()
                        narings_del_d = delar_d[1].strip()
                    else:
                        recept_text_clean_d = clean_text_d.strip()
                        narings_del_d = ""
                    
                    with st.expander(f"📌 {titel} ({kategori})"):
                        st.markdown(recept_text_clean_d, unsafe_allow_html=True)
                        if narings_del_d:
                            st.markdown("### 📊 Näringsinnehåll\n" + narings_del_d)
                        
                        st.divider()
                        
                        full_visnings_text_d = f"{recept_text_clean_d}\n\n### 📊 Näringsinnehåll\n{narings_del_d}" if narings_del_d else recept_text_clean_d
                        recept_innehall_d = f"Recept: {titel} ({kategori})\n\n{full_visnings_text_d}"
                        safe_text_d = json.dumps(recept_innehall_d)
                        
                        print_html_content_d = recept_text_clean_d.replace(chr(92), chr(92)*2).replace("'", "\\'").replace(chr(10), "<br>")
                        
                        narings_html_print_d = ""
                        if narings_del_d:
                            naring_lines_d = narings_del_d.split("\n")
                            table_rows_d = []
                            for line in naring_lines_d:
                                if "|" in line and "---" not in line:
                                    cols = [c.strip() for c in line.split("|") if c.strip() != ""]
                                    if cols:
                                        table_rows_d.append(cols)
                            if table_rows_d:
                                narings_html_print_d += "<h3>📊 Näringsinnehåll</h3><table>"
                                for i, row in enumerate(table_rows_d):
                                    tag = "th" if i == 0 else "td"
                                    narings_html_print_d += "<tr>"
                                    for col in row:
                                        narings_html_print_d += f"<{tag}>{col}</{tag}>"
                                    narings_html_print_d += "</tr>"
                                narings_html_print_d += "</table>"
                        
                        print_html_d = f"""
                        <!DOCTYPE html>
                        <html>
                        <head>
                        <meta charset="utf-8">
                        <style>
                          body {{ margin: 0; font-family: sans-serif; background-color: transparent; }}
                          .btn-container {{ display: flex; gap: 8px; flex-wrap: wrap; }}
                          button {{
                              background-color: #ff4b4b;
                              color: white;
                              border: none;
                              padding: 8px 14px;
                              border-radius: 6px;
                              font-weight: 600;
                              cursor: pointer;
                              font-size: 13px;
                              box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                          }}
                          button:hover {{ background-color: #ff2b2b; }}
                          #status {{ font-size: 13px; color: #666; align-self: center; }}
                        </style>
                        </head>
                        <body>
                            <div class="btn-container">
                                <button onclick="printRecipe()">🖨 Skriv ut recept</button>
                                <button onclick="copyRecipe()">📋 Kopiera text</button>
                                <span id="status"></span>
                            </div>

                            <script>
                            function printRecipe() {{
                                const win = window.open('', '_blank');
                                if (!win) {{
                                    alert('Tillåt popups i webbläsaren för att skriva ut.');
                                    return;
                                }}
                                
                                let content = `{print_html_content_d}`;
                                content = content.replace(/###\s*(.*?)(<br>|$)/g, '<h3 style="margin-top:20px; font-size:16px; font-weight:bold;">$1</h3>');

                                win.document.write('<!DOCTYPE html><html><head><meta charset="utf-8"><title>{titel}</title><style>body {{ font-family: sans-serif; padding: 30px; color: #111; line-height: 1.5; }} h2 {{ margin-bottom: 5px; }} p {{ color: #555; }} hr {{ border: 0; border-top: 1px solid #ddd; margin: 20px 0; }} table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }} th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }} th {{ background-color: #f4f4f4; }}</style></head><body>');
                                win.document.write('<h2>{titel}</h2>');
                                win.document.write('<p><em>Kategori: {kategori}</em></p>');
                                win.document.write('<hr/>');
                                win.document.write('<div>' + content + '</div>');
                                win.document.write('{narings_html_print_d}');
                                win.document.write('</body></html>');
                                win.document.close();
                                win.focus();
                                
                                setTimeout(() => {{
                                    win.print();
                                    win.close();
                                }}, 500);
                            }}

                            function copyRecipe() {{
                                const textToShare = {safe_text_d};
                                const statusEl = document.getElementById('status');
                                navigator.clipboard.writeText(textToShare).then(() => {{
                                    statusEl.innerText = 'Kopierat!';
                                    setTimeout(() => {{ statusEl.innerText = ''; }}, 2000);
                                }});
                            }}
                            </script>
                        </body>
                        </html>
                        """
                        components.html(print_html_d, height=45)
                                
    except Exception as e:
        st.error(f"Kunde inte hämta delade recept: {e}")