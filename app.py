import json
import os
import uuid
import pandas as pd
from PIL import Image
import streamlit as st
import streamlit.components.v1 as components
from google import genai
from supabase import Client, create_client

# --- SUPABASE & GEMINI CONFIG ---
SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.environ.get("SUPABASE_URL"))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", os.environ.get("SUPABASE_KEY"))
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

gemini_api_key = st.secrets.get(
    "GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY")
)
client = genai.Client(api_key=gemini_api_key)

# HÄR STYR DU MODELLEN TILL 3.8-FLASH ENLIGT FELMEDDELANDET:
GEMINI_MODEL = "gemini-3.8-flash"

# --- VERSIONSHANTERING ---
VERSION = "v1.35"
st.title(f"📖 Receptboken med AI-skanning ({VERSION})")

# --- INLOGGNINGSHANTERING ---
if "user" not in st.session_state:
  st.session_state["user"] = None

if not st.session_state["user"]:
  st.subheader("🔑 Logga in")

  with st.form("login_form"):
    email = st.text_input("E-post", key="login_email", autocomplete="username")
    password = st.text_input(
        "Lösenord",
        type="password",
        key="login_password",
        autocomplete="current-password",
    )

    col1, col2 = st.columns(2)
    with col1:
      submit_login = st.form_submit_button("Logga in")
    with col2:
      submit_signup = st.form_submit_button("Skapa konto")

    if submit_login:
      try:
        auth_res = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password,
        })
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
flik1, flik2, flik3 = st.tabs(
    ["📷 Skanna Recept", "📚 Mina Recept", "👥 Delade Böcker"]
)

with flik1:
  st.header("Skanna handskrivet recept")

  uppuploadad_bild = st.file_uploader(
      "Välj bild på recept...",
      type=["jpg", "jpeg", "png"],
      key=f"uploader_{st.session_state['uploader_key']}",
  )

  if uppuploadad_bild:
    bild = Image.open(uppuploadad_bild)
    bild.thumbnail((1000, 1000))
    st.image(bild, caption="Uppladdad bild (optimerad)", width=300)

    if st.button("Analysera recept med AI", key="btn_analysera"):
      with st.spinner(
          "Analyserar handstil och beräknar näringsvärden & vitaminer..."
      ):
        try:
          prompt = (
              "Analysera denna bild av ett recept. "
              "Returnera svaret strikt som en JSON-struktur med följande"
              " nycklar:\n"
              "- 'title': Receptets namn\n"
              "- 'category': Passande kategori (t.ex. Huvudrätt, Efterrätt,"
              " Bakverk)\n"
              "- 'ingredients': Endast ingredienslistan renskriven (sträng).\n"
              "- 'instructions': Endast tillagningsstegen renskrivna (sträng,"
              " helt fri från näringsrubriker).\n"
              "- 'nutrition': Ett objekt med uppskattade näringsvärden"
              " uppdelade i 'per_portion' och 'per_100g'. Inkludera nycklarna:"
              " 'energi' (kcal/kJ), 'protein' (g), 'kolhydrater' (g), 'socker'"
              " (g), 'fett' (g), 'mattat_fett' (g), 'fiber' (g), 'salt' (g),"
              " samt mikronäringsvärden: 'c_vitamin' (mg), 'd_vitamin' (µg),"
              " 'jarn' (mg).\n"
              "Svara ENDAST med giltig JSON utan markdown-kodblock."
          )

          # Använder den uppdaterade modellen här
          response = client.models.generate_content(
              model=GEMINI_MODEL, contents=[bild, prompt]
          )

          raw_text = response.text.strip()
          if raw_text.startswith("```json"):
            raw_text = (
                raw_text.replace("```json", "").replace("```", "").strip()
            )

          recept_data = json.loads(raw_text)
          st.session_state["analyserat_recept"] = recept_data
          st.success("Receptet har analyserats!")

        except Exception as e:
          st.error(
              f"Ett fel uppstod vid anropet till Gemini (modell:"
              f" {GEMINI_MODEL}): {e}"
          )

    if "analyserat_recept" in st.session_state:
      rec = st.session_state["analyserat_recept"]

      st.subheader("Granska & Spara")

      col_titel, col_kat = st.columns([2, 1])
      with col_titel:
        titel_input = st.text_input("Titel", value=rec.get("title", ""))
      with col_kat:
        kategori_input = st.text_input(
            "Kategori", value=rec.get("category", "Övrigt")
        )

      ingredients_input = st.text_area(
          "Ingredienser", value=rec.get("ingredients", ""), height=150
      )
      instructions_input = st.text_area(
          "Tillagningssteg", value=rec.get("instructions", ""), height=150
      )

      st.markdown("### 📊 Näringsinnehåll & Vitaminer")

      naring_data = rec.get("nutrition", {})
      p_data = naring_data.get("per_portion", {})
      h_data = naring_data.get("per_100g", {})

      df_naring = pd.DataFrame({
          "Näringsämne": [
              "⚡ Energi",
              "🥩 Protein",
              "🍞 Kolhydrater",
              "varav sockerarter",
              "🥑 Fett",
              "varav mättat fett",
              "🌾 Fiber",
              "🧂 Salt",
              "🍊 C-vitamin",
              "☀ D-vitamin",
              "🧲 Järn",
          ],
          "Per 100 g": [
              h_data.get("energi", "-"),
              h_data.get("protein", "-"),
              h_data.get("kolhydrater", "-"),
              h_data.get("socker", "-"),
              h_data.get("fett", "-"),
              h_data.get("mattat_fett", "-"),
              h_data.get("fiber", "-"),
              h_data.get("salt", "-"),
              h_data.get("c_vitamin", "-"),
              h_data.get("d_vitamin", "-"),
              h_data.get("jarn", "-"),
          ],
          "Per Portion": [
              p_data.get("energi", "-"),
              p_data.get("protein", "-"),
              p_data.get("kolhydrater", "-"),
              p_data.get("socker", "-"),
              p_data.get("fett", "-"),
              p_data.get("mattat_fett", "-"),
              p_data.get("fiber", "-"),
              p_data.get("salt", "-"),
              p_data.get("c_vitamin", "-"),
              p_data.get("d_vitamin", "-"),
              p_data.get("jarn", "-"),
          ],
      })

      st.table(df_naring)

      if st.button("💾 Spara till databasen", key="btn_spara"):
        with st.spinner("Laddar upp bild och sparar recept..."):
          try:
            existing_meta = (
                supabase.table("recept")
                .select("bok_namn, is_public")
                .eq("user_id", st.session_state["user"].id)
                .limit(1)
                .execute()
            )
            current_bok_namn = "Anonymkokboken"
            current_is_public = False
            if existing_meta.data and len(existing_meta.data) > 0:
              current_bok_namn = (
                  existing_meta.data[0].get("bok_namn") or "Anonymkokboken"
              )
              current_is_public = bool(
                  existing_meta.data[0].get("is_public", False)
              )

            bild_url = None
            if uppuploadad_bild is not None:
              file_ext = uppuploadad_bild.name.split(".")[-1]
              file_name = (
                  f"{st.session_state['user'].id}/{uuid.uuid4()}.{file_ext}"
              )

              uppuploadad_bild.seek(0)

              supabase.storage.from_("recept-bilder").upload(
                  path=file_name,
                  file=uppuploadad_bild.getvalue(),
                  file_options={"content-type": f"image/{file_ext}"},
              )

              public_url_res = supabase.storage.from_("recept-bilder").get_public_url(
                  file_name
              )
              bild_url = public_url_res

            data_att_spara = {
                "user_id": st.session_state["user"].id,
                "title": titel_input,
                "category": kategori_input,
                "ingredients": ingredients_input,
                "instructions": instructions_input,
                "nutrition_json": naring_data,
                "bild_url": bild_url,
                "is_public": current_is_public,
                "bok_namn": current_bok_namn,
            }

            supabase.table("recept").insert(data_att_spara).execute()
            st.success("Recept och bild har sparats!")

            del st.session_state["analyserat_recept"]
            st.session_state["uploader_key"] += 1

            st.rerun()
          except Exception as e:
            st.error(f"Kunde inte spara till databasen: {e}")

with flik2:
  st.header("Mina sparade recept")

  with st.expander("⚙ Inställningar för delning & Namn"):
    nuvarande_status = False
    nuvarande_bok_namn = "Min Receptbok"
    try:
      user_check = (
          supabase.table("recept")
          .select("is_public, bok_namn")
          .eq("user_id", st.session_state["user"].id)
          .limit(1)
          .execute()
      )
      if user_check.data and len(user_check.data) > 0:
        nuvarande_status = bool(user_check.data[0].get("is_public", False))
        nuvarande_bok_namn = (
            user_check.data[0].get("bok_namn") or "Min Receptbok"
        )
    except Exception as e:
      pass

    input_bok_namn = st.text_input(
        "Namn på din receptbok (visas för andra)", value=nuvarande_bok_namn
    )
    gör_publik = st.checkbox(
        "🌍 Gör hela min receptbok publik (synlig under Delade böcker)",
        value=nuvarande_status,
    )

    if st.button("Uppdatera delningsinställningar"):
      try:
        supabase.table("recept").update({
            "is_public": gör_publik,
            "bok_namn": input_bok_namn,
        }).eq("user_id", st.session_state["user"].id).execute()
        st.success("Inställningarna har sparats!")
        st.rerun()
      except Exception as e:
        st.error(f"Kunde inte uppdatera inställningar: {e}")

  sokord = st.text_input("🔍 Sök i dina recept...", "", key="sok_recept")

  try:
    res = (
        supabase.table("recept")
        .select("*")
        .eq("user_id", st.session_state["user"].id)
        .execute()
    )
    recept_lista = res.data

    if not recept_lista:
      st.info("Inga sparade recept hittades.")
    else:
      for r in recept_lista:
        titel = r.get("title") or r.get("titel") or "Namnlöst recept"
        kategori = r.get("category") or r.get("kategori") or "Övrigt"
        ingredients = r.get("ingredients") or ""
        instructions = r.get("instructions") or ""

        if not ingredients and not instructions and r.get("text"):
          ingredients = r.get("text")

        bild_url = r.get("bild_url")
        recept_id = r.get("id")
        nutrition_dict = r.get("nutrition_json") or {}

        if (
            sokord.lower() in str(titel).lower()
            or sokord.lower() in str(ingredients).lower()
            or sokord.lower() in str(instructions).lower()
        ):
          with st.expander(f"📌 {titel} ({kategori})"):
            edit_key = f"edit_mode_{recept_id}"
            if edit_key not in st.session_state:
              st.session_state[edit_key] = False

            col_visa1, col_visa2 = st.columns([10, 2])
            with col_visa2:
              if st.button(
                  "✏️ Redigera", key=f"btn_toggle_edit_{recept_id}"
              ):
                st.session_state[edit_key] = not st.session_state[edit_key]
                st.rerun()

            if st.session_state[edit_key]:
              ny_titel = st.text_input(
                  "Titel", value=titel, key=f"ny_titel_{recept_id}"
              )
              ny_kategori = st.text_input(
                  "Kategori", value=kategori, key=f"ny_kat_{recept_id}"
              )
              ny_ingredients = st.text_area(
                  "Ingredienser",
                  value=ingredients,
                  height=150,
                  key=f"ny_ing_{recept_id}",
              )
              ny_instructions = st.text_area(
                  "Tillagningssteg",
                  value=instructions,
                  height=150,
                  key=f"ny_inst_{recept_id}",
              )

              col_spara, col_avbryt = st.columns(2)
              with col_spara:
                if st.button("💾 Spara ändringar", key=f"save_{recept_id}"):
                  try:
                    supabase.table("recept").update({
                        "title": ny_titel,
                        "category": ny_kategori,
                        "ingredients": ny_ingredients,
                        "instructions": ny_instructions,
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
              if bild_url:
                st.image(bild_url, caption="Skannat recept", width=300)

              st.markdown("### 🛒 Ingredienser")
              st.markdown(ingredients)

              st.markdown("### 👨‍🍳 Tillagning")
              st.markdown(instructions)

              if nutrition_dict and isinstance(nutrition_dict, dict):
                p_data = nutrition_dict.get("per_portion", {})
                h_data = nutrition_dict.get("per_100g", {})
                if p_data or h_data:
                  st.markdown("### 📊 Näringsinnehåll & Vitaminer")
                  df_naring_vis = pd.DataFrame({
                      "Näringsämne": [
                          "⚡ Energi",
                          "🥩 Protein",
                          "🍞 Kolhydrater",
                          "varav sockerarter",
                          "🥑 Fett",
                          "varav mättat fett",
                          "🌾 Fiber",
                          "🧂 Salt",
                          "🍊 C-vitamin",
                          "☀️ D-vitamin",
                          "🧲 Järn",
                      ],
                      "Per 100 g": [
                          h_data.get("energi", "-"),
                          h_data.get("protein", "-"),
                          h_data.get("kolhydrater", "-"),
                          h_data.get("socker", "-"),
                          h_data.get("fett", "-"),
                          h_data.get("mattat_fett", "-"),
                          h_data.get("fiber", "-"),
                          h_data.get("salt", "-"),
                          h_data.get("c_vitamin", "-"),
                          h_data.get("d_vitamin", "-"),
                          h_data.get("jarn", "-"),
                      ],
                      "Per Portion": [
                          p_data.get("energi", "-"),
                          p_data.get("protein", "-"),
                          p_data.get("kolhydrater", "-"),
                          p_data.get("socker", "-"),
                          p_data.get("fett", "-"),
                          p_data.get("mattat_fett", "-"),
                          p_data.get("fiber", "-"),
                          p_data.get("salt", "-"),
                          p_data.get("c_vitamin", "-"),
                          p_data.get("d_vitamin", "-"),
                          p_data.get("jarn", "-"),
                      ],
                  })
                  st.table(df_naring_vis)

              st.divider()

              full_visnings_text = (
                  f"### 🛒 Ingredienser\n{ingredients}\n\n### 👨‍🍳"
                  f" Tillagning\n{instructions}"
              )
              recept_innehall = (
                  f"Recept: {titel} ({kategori})\n\n{full_visnings_text}"
              )
              safe_text = json.dumps(recept_innehall)

              print_html_content = (
                  f"<h3>🛒 Ingredienser</h3><p>{ingredients.replace(chr(10), '<br>')}</p>"
                  f"<h3>👨‍🍳 Tillagning</h3><p>{instructions.replace(chr(10), '<br>')}</p>"
              )

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
                                    
                                    win.document.write('<!DOCTYPE html><html><head><meta charset="utf-8"><title>{titel}</title><style>body {{ font-family: sans-serif; padding: 30px; color: #111; line-height: 1.5; }} h2 {{ margin-bottom: 5px; }} p {{ color: #555; }} hr {{ border: 0; border-top: 1px solid #ddd; margin: 20px 0; }} h3 {{ margin-top: 20px; font-size: 16px; }}</style></head><body>');
                                    win.document.write('<h2>{titel}</h2>');
                                    win.document.write('<p><em>Kategori: {kategori}</em></p>');
                                    win.document.write('<hr/>');
                                    win.document.write('<div>{print_html_content}</div>');
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
                supabase.table("recept").delete().eq(
                    "id", recept_id
                ).execute()
                st.success("Receptet raderades!")
                st.rerun()

  except Exception as e:
    st.error(f"Kunde inte hämta recept från databasen: {e}")

with flik3:
  st.header("👥 Delade receptböcker")
  st.markdown(
      "Här kan du välja och läsa andra användares publika receptböcker via"
      " rullgardinsmenyn nedan."
  )

  try:
    publika_res = (
        supabase.table("recept").select("*").eq("is_public", True).execute()
    )
    publika_recept = publika_res.data

    if not publika_recept:
      st.info("Inga publika receptböcker hittades just nu.")
    else:
      användare_dict = {}
      bok_namn_dict = {}
      for rec in publika_recept:
        uid = rec.get("user_id")
        b_namn = rec.get("bok_namn") or f"Receptbok ({uid[:6]}...)"
        if uid not in användare_dict:
          användare_dict[uid] = []
          bok_namn_dict[uid] = b_namn
        användare_dict[uid].append(rec)

      vald_användare = st.selectbox(
          "Välj receptbok att kika i:",
          options=list(användare_dict.keys()),
          format_func=lambda x: f"📖 {bok_namn_dict.get(x, 'Receptbok')}",
      )

      if vald_användare:
        st.divider()
        st.subheader(
            "📖 Receptsamling:"
            f" {bok_namn_dict.get(vald_användare, 'Receptbok')}"
        )

        for r in användare_dict[vald_användare]:
          titel = r.get("title") or r.get("titel") or "Namnlöst recept"
          kategori = r.get("category") or r.get("kategori") or "Övrigt"
          ingredients_d = r.get("ingredients") or r.get("text") or ""
          instructions_d = r.get("instructions") or ""
          bild_url_d = r.get("bild_url")
          nutrition_dict_d = r.get("nutrition_json") or {}

          with st.expander(f"📌 {titel} ({kategori})"):
            if bild_url_d:
              st.image(bild_url_d, caption="Skannat recept", width=300)

            st.markdown("### 🛒 Ingredienser")
            st.markdown(ingredients_d)

            st.markdown("### 👨‍🍳 Tillagning")
            st.markdown(instructions_d)

            if nutrition_dict_d and isinstance(nutrition_dict_d, dict):
              p_data_d = nutrition_dict_d.get("per_portion", {})
              h_data_d = nutrition_dict_d.get("per_100g", {})
              if p_data_d or h_data_d:
                st.markdown("### 📊 Näringsinnehåll & Vitaminer")
                df_naring_vis_d = pd.DataFrame({
                    "Näringsämne": [
                        "⚡ Energi",
                        "🥩 Protein",
                        "🍞 Kolhydrater",
                        "varav sockerarter",
                        "🥑 Fett",
                        "varav mättat fett",
                        "🌾 Fiber",
                        "🧂 Salt",
                        "🍊 C-vitamin",
                        "☀️ D-vitamin",
                        "🧲 Järn",
                    ],
                    "Per 100 g": [
                        h_data_d.get("energi", "-"),
                        h_data_d.get("protein", "-"),
                        h_data_d.get("kolhydrater", "-"),
                        h_data_d.get("socker", "-"),
                        h_data_d.get("fett", "-"),
                        h_data_d.get("mattat_fett", "-"),
                        h_data_d.get("fiber", "-"),
                        h_data_d.get("salt", "-"),
                        h_data_d.get("c_vitamin", "-"),
                        h_data_d.get("d_vitamin", "-"),
                        h_data_d.get("jarn", "-"),
                    ],
                    "Per Portion": [
                        p_data_d.get("energi", "-"),
                        p_data_d.get("protein", "-"),
                        p_data_d.get("kolhydrater", "-"),
                        p_data_d.get("socker", "-"),
                        p_data_d.get("fett", "-"),
                        p_data_d.get("mattat_fett", "-"),
                        p_data_d.get("fiber", "-"),
                        p_data_d.get("salt", "-"),
                        p_data_d.get("c_vitamin", "-"),
                        p_data_d.get("d_vitamin", "-"),
                        p_data_d.get("jarn", "-"),
                    ],
                })
                st.table(df_naring_vis_d)

  except Exception as e:
    st.error(f"Kunde inte hämta delade recept: {e}")