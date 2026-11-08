import os
import json
import uuid
import re
import pandas as pd
from PIL import Image
import streamlit as st
import streamlit.components.v1 as components
from supabase import create_client, Client
from google import genai

# --- KEEP-ALIVE (Förhindrar timeout vid inaktivitet) ---
components.html(
    """
    <script>
    setInterval(function() {
        window.dispatchEvent(new Event('resize'));
    }, 240000); // Pingar servern var 4:e minut
    </script>
    """,
    height=0
)

# --- SUPABASE & GEMINI CONFIG ---
SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.environ.get("SUPABASE_URL"))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", os.environ.get("SUPABASE_KEY"))
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

gemini_api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY"))
client = genai.Client(api_key=gemini_api_key)

GEMINI_MODEL = "gemini-3.8-flash"

# --- VERSIONSHANTERING ---
VERSION = "v1.67"
st.title(f"📖 Receptboken med AI-skanning ({VERSION})")

# --- HJÄLPER-FUNKTIONER FÖR FORMATERING & PORTIONSOMRÄKNING ---
def formatera_ingredienser(text):
    """Säkerställer att varje ingrediens visas på en ny rad med en punkt framför."""
    if not text:
        return ""
    rader = [r.strip() for r in str(text).split("\n") if r.strip()]
    formaterade = []
    for r in rader:
        if not r.startswith("-") and not r.startswith("•") and not r.startswith("*"):
            formaterade.append(f"- {r}")
        else:
            formaterade.append(r)
    return "\n".join(formaterade)

def ta_bort_numrering(text):
    """Tar bort numrering i början av rader (t.ex. '1. ', '2) ') så att tillagningen visas utan nummer."""
    if not text:
        return ""
    rader = str(text).split("\n")
    rensade = []
    for r in rader:
        rensad_rad = re.sub(r'^\s*\d+[\.$$\:]\s*', '', r)
        rensade.append(rensad_rad)
    return "\n".join(rensade)

def skala_ingredienser(text, original_servings, target_servings):
    """Skalar om siffervärden i ingredienstexten baserat på förhållandet mellan målportioner och originalportioner."""
    if not text or not original_servings or original_servings <= 0 or original_servings == target_servings:
        return text

    factor = target_servings / original_servings

    def convert_match(match):
        val_str = match.group(0)
        if '/' in val_str:
            parts = val_str.split('/')
            try:
                num = float(parts[0]) / float(parts[1])
            except (ValueError, ZeroDivisionError):
                return val_str
        else:
            val_str_clean = val_str.replace(',', '.')
            try:
                num = float(val_str_clean)
            except ValueError:
                return val_str

        scaled_num = num * factor
        if scaled_num.is_integer():
            return str(int(scaled_num))
        else:
            return f"{scaled_num:.2f}".rstrip('0').rstrip('.').replace('.', ',')

    pattern = r'\b\d+(?:[\/\.,]\d+)?\b'
    rader = str(text).split("\n")
    skalade_rader = [re.sub(pattern, convert_match, rad) for rad in rader]
    return "\n".join(skalade_rader)

def rendera_receptvisning(rec_data, unik_nyckel, ta_med_naring=True, kompakt=False):
    """Delad receptvisning. kompakt=True ger mindre rubriker, mindre bild och tajtare layout."""
    titel = rec_data.get("title") or rec_data.get("titel") or "Namnlöst recept"
    kategori = rec_data.get("category") or rec_data.get("kategori") or "Övrigt"
    ingredients = rec_data.get("ingredients") or ""
    instructions = rec_data.get("instructions") or ""
    orig_portioner = rec_data.get("portioner") or rec_data.get("servings") or 4
    orig_enhet = rec_data.get("portioner_enhet") or "portioner"
    image_url = rec_data.get("image_url")
    nutrition_dict = rec_data.get("nutrition_json") or {}

    if not ingredients and not instructions and rec_data.get("text"):
        ingredients = rec_data.get("text")

    if image_url:
        st.image(image_url, caption="Skannat recept", width=160 if kompakt else 300)

    col_p1, col_p2 = st.columns([1, 2])
    with col_p1:
        valda_portioner = st.number_input(
            f"Antal ({orig_enhet}):",
            min_value=1, max_value=500, value=int(orig_portioner), step=1,
            key=f"portioner_{unik_nyckel}"
        )
    if valda_portioner != orig_portioner:
        st.caption(f"ℹ️ Omräknat från {orig_portioner} {orig_enhet} till {valda_portioner} {orig_enhet}.")

    if kompakt:
        st.caption("🛒 Ingredienser")
        st.markdown(formatera_ingredienser(skala_ingredienser(ingredients, orig_portioner, valda_portioner)))
        st.caption("👨‍🍳 Tillagning")
        st.markdown(ta_bort_numrering(instructions))
    else:
        st.markdown("### 🛒 Ingredienser")
        st.markdown(formatera_ingredienser(skala_ingredienser(ingredients, orig_portioner, valda_portioner)))
        st.markdown("### 👨‍🍳 Tillagning")
        st.markdown(ta_bort_numrering(instructions))

    if ta_med_naring and nutrition_dict and isinstance(nutrition_dict, dict):
        p_data = nutrition_dict.get("per_portion", {})
        h_data = nutrition_dict.get("per_100g", {})
        if p_data or h_data:
            if kompakt:
                st.caption("📊 Näringsinnehåll & Vitaminer")
            else:
                st.markdown("### 📊 Näringsinnehåll & Vitaminer")
            df_naring_vis = pd.DataFrame({
                "Näringsämne": ["⚡ Energi", "🥩 Protein", "🍞 Kolhydrater", "varav sockerarter", "🥑 Fett", "varav mättat fett", "🌾 Fiber", "🧂 Salt", "🍊 C-vitamin", "☀️ D-vitamin", "🧪 Järn"],
                "Per 100 g": [h_data.get("energi", "-"), h_data.get("protein", "-"), h_data.get("kolhydrater", "-"), h_data.get("socker", "-"), h_data.get("fett", "-"), h_data.get("mattat_fett", "-"), h_data.get("fiber", "-"), h_data.get("salt", "-"), h_data.get("c_vitamin", "-"), h_data.get("d_vitamin", "-"), h_data.get("jarn", "-")],
                "Per Portion": [p_data.get("energi", "-"), p_data.get("protein", "-"), p_data.get("kolhydrater", "-"), p_data.get("socker", "-"), p_data.get("fett", "-"), p_data.get("mattat_fett", "-"), p_data.get("fiber", "-"), p_data.get("salt", "-"), p_data.get("c_vitamin", "-"), p_data.get("d_vitamin", "-"), p_data.get("jarn", "-")]
            })
            st.table(df_naring_vis)

    return titel, kategori

# --- INLOGGNINGSHANTERING ---
if "user" not in st.session_state:
    st.session_state["user"] = None

if not st.session_state["user"]:
    st.subheader("🔑 Logga in")

    with st.form("login_form"):
        email = st.text_input("E-post", key="login_email", autocomplete="username")
        password = st.text_input("Lösenord", type="password", key="login_password", autocomplete="current-password")

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

# --- SIDOMENY (SIDEBAR) ---
with st.sidebar:
    st.header("⚙️ Konto & Inställningar")
    st.write(f"Inloggad som:\n**{st.session_state['user'].email}**")

    st.divider()

    # --- KNAPP FÖR ATT HÅLLA SKÄRMEN TÄND (WAKE LOCK) ---
    st.subheader("📱 Skärminställning")
    wake_lock_html = """
    <div style="margin-bottom: 15px;">
        <button id="wakelock-btn" onclick="toggleWakeLock()" style="
            background-color: #4CAF50;
            color: white;
            border: none;
            padding: 10px 16px;
            border-radius: 8px;
            font-weight: bold;
            cursor: pointer;
            width: 100%;
            font-size: 14px;
        ">💡 Håll skärmen tänd: AV</button>
        <p id="wakelock-status" style="font-size: 12px; color: #666; margin-top: 5px; text-align: center;"></p>
    </div>

    <script>
    let wakeLock = null;

    async function toggleWakeLock() {
        const btn = document.getElementById('wakelock-btn');
        const status = document.getElementById('wakelock-status');

        if ('wakeLock' in navigator) {
            if (wakeLock === null) {
                try {
                    wakeLock = await navigator.wakeLock.request('screen');
                    btn.innerText = '💡 Håll skärmen tänd: PÅ';
                    btn.style.backgroundColor = '#ff9800';
                    status.innerText = 'Skärmen förblir tänd';
                } catch (err) {
                    status.innerText = 'Kunde inte aktivera: ' + err.message;
                }
            } else {
                wakeLock.release();
                wakeLock = null;
                btn.innerText = '💡 Håll skärmen tänd: AV';
                btn.style.backgroundColor = '#4CAF50';
                status.innerText = 'Normal skärmsläckning';
            }
        } else {
            status.innerText = 'Din webbläsare stöder inte Screen Wake Lock API.';
        }
    }
    </script>
    """
    components.html(wake_lock_html, height=80)

    st.divider()

    st.subheader("Mina delningsinställningar")
    nuvarande_status = False
    nuvarande_book_name = "Min Receptbok"
    try:
        user_check = supabase.table("recept").select("is_public, book_name").eq("user_id", st.session_state["user"].id).limit(1).execute()
        if user_check.data and len(user_check.data) > 0:
            nuvarande_status = bool(user_check.data[0].get("is_public", False))
            nuvarande_book_name = user_check.data[0].get("book_name") or "Min Receptbok"
    except Exception:
        pass

    input_book_name = st.text_input("Namn på din receptbok", value=nuvarande_book_name)
    gör_publik = st.checkbox("🌐 Gör hela receptboken publik", value=nuvarande_status)

    if st.button("Spara inställningar"):
        try:
            supabase.table("recept").update({
                "is_public": gör_publik,
                "book_name": input_book_name
            }).eq("user_id", st.session_state["user"].id).execute()
            st.success("Inställningarna har sparats!")
            st.rerun()
        except Exception as e:
            st.error(f"Kunde inte uppdatera: {e}")

    st.divider()

    st.subheader("👥 Andras Receptböcker")
    try:
        publika_res = supabase.table("recept").select("*").eq("is_public", True).execute()
        publika_recept = publika_res.data

        if not publika_recept:
            st.info("Inga publika receptböcker hittades.")
            st.session_state["vald_delad_användare"] = None
        else:
            användare_dict = {}
            book_name_dict = {}
            for rec in publika_recept:
                uid = rec.get("user_id")
                b_namn = rec.get("book_name") or f"Receptbok ({uid[:6]}...)"
                if uid not in användare_dict:
                    användare_dict[uid] = []
                    book_name_dict[uid] = b_namn
                användare_dict[uid].append(rec)

            options_list = [None] + list(användare_dict.keys())
            def format_func(x):
                if x is None:
                    return "🏠 Visa Mina Egna Recept"
                return f"📖 {book_name_dict.get(x, 'Receptbok')}"

            vald_användare = st.selectbox("Välj bok att läsa:", options=options_list, format_func=format_func, key="select_delad_bok")
            st.session_state["vald_delad_användare"] = vald_användare
            st.session_state["användare_dict"] = användare_dict
            st.session_state["book_name_dict"] = book_name_dict

    except Exception:
        st.error("Kunde inte hämta delade böcker.")
        st.session_state["vald_delad_användare"] = None

    st.divider()

    if st.button("Logga ut", type="secondary"):
        try:
            supabase.auth.sign_out()
        except Exception:
            pass
        st.session_state["user"] = None
        st.rerun()

if "uploader_key" not in st.session_state:
    st.session_state["uploader_key"] = 0

aktiv_delad_användare = st.session_state.get("vald_delad_användare", None)

# --- OM EN DELAD BOK ÄR VALD I SIDOMENYN ---
if aktiv_delad_användare:
    b_namn = st.session_state["book_name_dict"].get(aktiv_delad_användare, "Receptbok")
    st.info(f"📖 Du läser nu den publika receptboken: **{b_namn}**")

    sokord_delat = st.text_input("🔍 Sök i denna publika receptbok...", "", key="sok_delat")
    delade_recept_lista = st.session_state["användare_dict"].get(aktiv_delad_användare, [])

    for r in delade_recept_lista:
        titel = r.get("title") or r.get("titel") or "Namnlöst recept"
        kategori = r.get("category") or r.get("kategori") or "Övrigt"
        ingredients_d = r.get("ingredients") or r.get("text") or ""
        instructions_d = r.get("instructions") or ""

        if not sokord_delat or sokord_delat.lower() in str(titel).lower() or sokord_delat.lower() in str(ingredients_d).lower() or sokord_delat.lower() in str(instructions_d).lower():
            with st.expander(f"📌 {titel} ({kategori})"):
                rendera_receptvisning(r, f"delat_{r.get('id', uuid.uuid4())}")

else:
    # --- VANLIGA HUVUDAPPEN (MIN BOK) ---
    flik1, flik2, flik3 = st.tabs(["📷 Skanna Recept", "📚 Mina Recept", "🔍 Sök Recept"])

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
                with st.spinner("Analyserar handstil, portionsantal och beräknar näringsvärden & vitaminer..."):
                    try:
                        prompt = (
                    f"Generera ÅTTA (8) goda recept som matchar följande ingredienser: {ingrediens_input}. "
                    f"Undvik dessa redan föreslagna titlar: {', '.join(befintliga_titlar) if befintliga_titlar else 'inga'}. "
                    "Generera alla åtta, sammanfatta inte och sluta inte i förtid. "
                    "Returnera ett recept i taget som enskilda JSON-objekt, ETT per rad, utan array-klamrar och utan mellanslag mellan objekten. "
                    "Varje objekt ska ha nycklarna:\n"
                    "- 'title': Receptets namn\n"
                    "- 'category': Kategori (t.ex. Förrätt, Huvudrätt, Efterrätt, Bakverk)\n"
                    "- 'portioner_antal': Antal portioner som heltal\n"
                    "- 'portioner_enhet': Enhet (t.ex. portioner)\n"
                    "- 'ingredients': Ingredienser på egen rad, med bindestreck i början\n"
                    "- 'instructions': Tillagningssteg utan numrering\n"
                    "- 'nutrition': Objekt med 'per_portion' och 'per_100g': 'energi', 'protein', 'kolhydrater', 'socker', 'fett', 'mattat_fett', 'fiber', 'salt', 'c_vitamin', 'd_vitamin', 'jarn'"
                )

                        response = client.models.generate_content(
                            model=GEMINI_MODEL,
                            contents=[bild, prompt]
                        )

                        raw_text = response.text.strip()
                        if raw_text.startswith("```json"):
                            raw_text = raw_text.replace("```json", "").replace("```", "").strip()

                        recept_data = json.loads(raw_text)
                        st.session_state["analyserat_recept"] = recept_data
                        st.success("Receptet har analyserats!")

                    except Exception as e:
                        st.error(f"Ett fel uppstod vid anropet till Gemini (modell: {GEMINI_MODEL}): {e}")

            if "analyserat_recept" in st.session_state:
                rec = st.session_state["analyserat_recept"]

                st.subheader("Granska & Spara")

                col_titel, col_kat, col_port, col_enh = st.columns([2, 1, 1, 1])
                with col_titel:
                    titel_input = st.text_input("Titel", value=rec.get("title", ""))
                with col_kat:
                    kategori_input = st.text_input("Kategori", value=rec.get("category", "Övrigt"))
                with col_port:
                    portioner_input = st.number_input("Antal", min_value=1, max_value=500, value=int(rec.get("portioner_antal", 4)))
                with col_enh:
                    enhet_input = st.text_input("Enhet", value=rec.get("portioner_enhet", "portioner"))

                ingredients_input = st.text_area("Ingredienser (en på varje rad)", value=formatera_ingredienser(rec.get("ingredients", "")), height=150)
                instructions_input = st.text_area("Tillagningssteg (utan numrering)", value=ta_bort_numrering(rec.get("instructions", "")), height=150)

                st.markdown("### 📊 Näringsinnehåll & Vitaminer")

                naring_data = rec.get("nutrition", {})
                p_data = naring_data.get("per_portion", {})
                h_data = naring_data.get("per_100g", {})

                df_naring = pd.DataFrame({
                    "Näringsämne": ["⚡ Energi", "🥩 Protein", "🍞 Kolhydrater", "varav sockerarter", "🥑 Fett", "varav mättat fett", "🌾 Fiber", "🧂 Salt", "🍊 C-vitamin", "☀️ D-vitamin", "🧪 Järn"],
                    "Per 100 g": [h_data.get("energi", "-"), h_data.get("protein", "-"), h_data.get("kolhydrater", "-"), h_data.get("socker", "-"), h_data.get("fett", "-"), h_data.get("mattat_fett", "-"), h_data.get("fiber", "-"), h_data.get("salt", "-"), h_data.get("c_vitamin", "-"), h_data.get("d_vitamin", "-"), h_data.get("jarn", "-")],
                    "Per Portion": [p_data.get("energi", "-"), p_data.get("protein", "-"), p_data.get("kolhydrater", "-"), p_data.get("socker", "-"), p_data.get("fett", "-"), p_data.get("mattat_fett", "-"), p_data.get("fiber", "-"), p_data.get("salt", "-"), p_data.get("c_vitamin", "-"), p_data.get("d_vitamin", "-"), p_data.get("jarn", "-")]
                })

                st.table(df_naring)

                if st.button("💾 Spara till databasen", key="btn_spara"):
                    with st.spinner("Laddar upp bild och sparar recept..."):
                        try:
                            existing_meta = supabase.table("recept").select("book_name, is_public").eq("user_id", st.session_state["user"].id).limit(1).execute()
                            current_book_name = "Anonymkokboken"
                            current_is_public = False
                            if existing_meta.data and len(existing_meta.data) > 0:
                                current_book_name = existing_meta.data[0].get("book_name") or "Anonymkokboken"
                                current_is_public = bool(existing_meta.data[0].get("is_public", False))

                            image_url = None
                            if uppuploadad_bild is not None:
                                file_ext = uppuploadad_bild.name.split(".")[-1]
                                file_name = f"{st.session_state['user'].id}/{uuid.uuid4()}.{file_ext}"

                                uppuploadad_bild.seek(0)

                                supabase.storage.from_("recept-bilder").upload(
                                    path=file_name,
                                    file=uppuploadad_bild.getvalue(),
                                    file_options={"content-type": f"image/{file_ext}"}
                                )

                                public_url_res = supabase.storage.from_("recept-bilder").get_public_url(file_name)
                                image_url = public_url_res

                            data_att_spara = {
                                "user_id": st.session_state["user"].id,
                                "title": titel_input,
                                "category": kategori_input,
                                "portioner": portioner_input,
                                "portioner_enhet": enhet_input,
                                "ingredients": ingredients_input,
                                "instructions": instructions_input,
                                "nutrition_json": naring_data,
                                "image_url": image_url,
                                "is_public": current_is_public,
                                "book_name": current_book_name
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
        sokord = st.text_input("🔍 Sök i dina recept...", "", key="sok_recept")

        try:
            res = supabase.table("recept").select("*").eq("user_id", st.session_state["user"].id).execute()
            recept_lista = res.data

            if not recept_lista:
                st.info("Inga sparade recept hittades.")
            else:
                for r in recept_lista:
                    titel = r.get("title") or r.get("titel") or "Namnlöst recept"
                    kategori = r.get("category") or r.get("kategori") or "Övrigt"
                    ingredients = r.get("ingredients") or ""
                    instructions = r.get("instructions") or ""
                    orig_portioner = r.get("portioner") or r.get("servings") or 4
                    orig_enhet = r.get("portioner_enhet") or "portioner"
                    image_url = r.get("image_url")
                    recept_id = r.get("id")
                    nutrition_dict = r.get("nutrition_json") or {}

                    if not ingredients and not instructions and r.get("text"):
                        ingredients = r.get("text")

                    if not sokord or sokord.lower() in str(titel).lower() or sokord.lower() in str(ingredients).lower() or sokord.lower() in str(instructions).lower():
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
                                ny_titel = st.text_input("Titel", value=titel, key=f"ny_titel_{recept_id}")
                                ny_kategori = st.text_input("Kategori", value=kategori, key=f"ny_kat_{recept_id}")
                                col_e1, col_e2 = st.columns(2)
                                with col_e1:
                                    ny_portioner = st.number_input("Antal (original)", min_value=1, max_value=500, value=int(orig_portioner), key=f"ny_port_{recept_id}")
                                with col_e2:
                                    ny_enhet = st.text_input("Enhet", value=orig_enhet, key=f"ny_enh_{recept_id}")

                                ny_ingredients = st.text_area("Ingredienser (en på varje rad)", value=formatera_ingredienser(ingredients), height=150, key=f"ny_ing_{recept_id}")
                                ny_instructions = st.text_area("Tillagningssteg (utan numrering)", value=ta_bort_numrering(instructions), height=150, key=f"ny_inst_{recept_id}")

                                col_spara, col_avbryt = st.columns(2)
                                with col_spara:
                                    if st.button("💾 Spara ändringar", key=f"save_{recept_id}"):
                                        try:
                                            supabase.table("recept").update({
                                                "title": ny_titel,
                                                "category": ny_kategori,
                                                "portioner": ny_portioner,
                                                "portioner_enhet": ny_enhet,
                                                "ingredients": ny_ingredients,
                                                "instructions": ny_instructions
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
                                rendera_receptvisning(r, f"egen_{recept_id}")

                                st.divider()

                                naring_text_lines = []
                                n_items = [
                                    ("Energi", "energi"), ("Protein", "protein"), ("Kolhydrater", "kolhydrater"),
                                    ("varav sockerarter", "socker"), ("Fett", "fett"), ("varav mättat fett", "mattat_fett"),
                                    ("Fiber", "fiber"), ("Salt", "salt"), ("C-vitamin", "c_vitamin"),
                                    ("D-vitamin", "d_vitamin"), ("Järn", "jarn")
                                ]

                                valda_portioner = st.session_state.get(f"portioner_egen_{recept_id}", int(orig_portioner))
                                skalad_ing = skala_ingredienser(ingredients, orig_portioner, valda_portioner)

                                if nutrition_dict and isinstance(nutrition_dict, dict):
                                    p_data_c = nutrition_dict.get("per_portion", {})
                                    h_data_c = nutrition_dict.get("per_100g", {})
                                    for lbl, ky in n_items:
                                        hv = h_data_c.get(ky, "-")
                                        pv = p_data_c.get(ky, "-")
                                        if hv != "-" or pv != "-":
                                            naring_text_lines.append(f" - {lbl}: {hv} per 100g, {pv} per portion")

                                naring_copy_block = "\n\nNäringsinnehåll & Vitaminer:\n" + "\n".join(naring_text_lines) if naring_text_lines else ""
                                rensad_instruktion = ta_bort_numrering(instructions)
                                recept_innehall = f"Recept: {titel}\nKategori: {kategori}\n\nIngredienser ({valda_portioner} {orig_enhet}):\n{formatera_ingredienser(skalad_ing)}\n\nTillagning:\n{rensad_instruktion}{naring_copy_block}"
                                safe_text = json.dumps(recept_innehall)

                                print_naring_html = ""
                                if nutrition_dict and isinstance(nutrition_dict, dict):
                                    p_data_p = nutrition_dict.get("per_portion", {})
                                    h_data_p = nutrition_dict.get("per_100g", {})
                                    if p_data_p or h_data_p:
                                        print_naring_html = """
                                        <h3 style="margin-top: 25px; font-size: 16px; border-bottom: 1px solid #ccc; padding-bottom: 5px;">📊 Näringsinnehåll & Vitaminer</h3>
                                        <table>
                                            <thead>
                                                <tr style="background-color: #f2f2f2; border-bottom: 1px solid #ddd;">
                                                    <th style="text-align: left; padding: 6px;">Näringsämne</th>
                                                    <th style="text-align: left; padding: 6px;">Per 100 g</th>
                                                    <th style="text-align: left; padding: 6px;">Per Portion</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                        """
                                        for lbl, ky in n_items:
                                            hv = h_data_p.get(ky, "-")
                                            pv = p_data_p.get(ky, "-")
                                            print_naring_html += f"""
                                            <tr style="border-bottom: 1px solid #eee;">
                                                <td style="padding: 5px;">{lbl}</td>
                                                <td style="padding: 5px;">{hv}</td>
                                                <td style="padding: 5px;">{pv}</td>
                                            </tr>
                                            """
                                        print_naring_html += "</tbody></table>"

                                print_html_content = (
                                    f"<h3>🛒 Ingredienser ({valda_portioner} {orig_enhet})</h3><p>{formatera_ingredienser(skalad_ing).replace(chr(10), '<br>')}</p>"
                                    f"<h3>👨‍🍳 Tillagning</h3><p>{rensad_instruktion.replace(chr(10), '<br>')}</p>"
                                    f"{print_naring_html}"
                                )
                                safe_print_html = json.dumps(print_html_content)

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
                                        <button onclick="printRecipe()">🖨️ Skriv ut recept</button>
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

                                        const htmlBody = {safe_print_html};

                                        win.document.write('<!DOCTYPE html><html><head><meta charset="utf-8"><title>{titel}</title><style>body {{ font-family: sans-serif; padding: 30px; color: #111; line-height: 1.5; }} h2 {{ margin-bottom: 5px; }} p {{ color: #555; }} hr {{ border: 0; border-top: 1px solid #ddd; margin: 20px 0; }} h3 {{ margin-top: 20px; font-size: 16px; color: #222; }} table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }} th, td {{ border-bottom: 1px solid #eee; padding: 6px; text-align: left; }} th {{ background-color: #f9f9f9; }}</style></head><body>');
                                        win.document.write('<h2>{titel}</h2>');
                                        win.document.write('<p><em>Kategori: {kategori}</em></p>');
                                        win.document.write('<hr/>');
                                        win.document.write('<div>' + htmlBody + '</div>');
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
                                    st.success("Receptet har raderats!")
                                    st.rerun()

        except Exception as e:
            st.error(f"Ett fel uppstod vid hämtning av dina recept: {e}")

    with flik3:
        st.header("🔍 Sök recept")
        st.caption("Sök bland dina sparade recept och få nya AI-förslag baserat på ingredienser.")

        # Tätare radavstånd för AI-korten
        st.markdown(
            """
            <style>
            div[data-testid="stExpander"] div[data-testid="stMarkdownContainer"] p {
                margin: 0 0 2px 0 !important;
                line-height: 1.25 !important;
            }
            div[data-testid="stExpander"] div[data-testid="stCaptionContainer"] p {
                margin: 0 !important;
            }
            div[data-testid="stExpander"] h3 { margin: 4px 0 2px 0 !important; }
            </style>
            """,
            unsafe_allow_html=True
        )

        ingrediens_input = st.text_input(
            "Ingredienser (separera med kommatecken, t.ex. kyckling, ris):",
            key="sok_ingrediens_input"
        )

        if "sok_state" not in st.session_state:
            st.session_state["sok_state"] = None

        if ingrediens_input:
            if st.session_state["sok_state"] is None or st.session_state["sok_state"].get("input") != ingrediens_input:
                st.session_state["sok_state"] = {"input": ingrediens_input, "ai_forslag": [], "har_hamtat": False}

        if ingrediens_input and st.session_state["sok_state"] is not None:
            state = st.session_state["sok_state"]
            sokte_ingredienser = [i.strip().lower() for i in ingrediens_input.split(",") if i.strip()]

            try:
                res = supabase.table("recept").select("*").eq("user_id", st.session_state["user"].id).execute()
                egna_recept = res.data or []
                egna_traffar = []
                for r in egna_recept:
                    sok_text = (str(r.get("title", "") + " " + r.get("ingredients", ""))).lower()
                    matches = sum(1 for ing in sokte_ingredienser if ing in sok_text)
                    if matches > 0:
                        egna_traffar.append((matches, r))
                egna_traffar.sort(key=lambda x: x[0], reverse=True)
            except Exception as e:
                st.error(f"Kunde inte hämta recept: {e}")
                egna_traffar = []

            if egna_traffar:
                st.subheader("Dina sparade recept")
                for score, rec_data in egna_traffar:
                    r_id = rec_data.get("id", uuid.uuid4())
                    titel_e = rec_data.get("title") or rec_data.get("titel") or "Namnlöst recept"
                    kategori_e = rec_data.get("category") or rec_data.get("kategori") or "Övrigt"
                    with st.expander(f"⭐ {titel_e} — {kategori_e}", expanded=False):
                        rendera_receptvisning(rec_data, f"sok_egen_{r_id}", kompakt=True)

            st.subheader("AI-förslag")
            ai_plats = st.container()

            def normalisera(rd):
                return {
                    "title": rd.get("title"),
                    "category": rd.get("category"),
                    "portioner": rd.get("portioner") or rd.get("portioner_antal") or 4,
                    "portioner_enhet": rd.get("portioner_enhet") or "portioner",
                    "ingredients": rd.get("ingredients"),
                    "instructions": rd.get("instructions"),
                    "nutrition_json": rd.get("nutrition") or {}
                }

            def spara_ai(norm):
                try:
                    existing_meta = supabase.table("recept").select("book_name, is_public").eq("user_id", st.session_state["user"].id).limit(1).execute()
                    current_book_name = "Anonymkokboken"
                    current_is_public = False
                    if existing_meta.data and len(existing_meta.data) > 0:
                        current_book_name = existing_meta.data[0].get("book_name") or "Anonymkokboken"
                        current_is_public = bool(existing_meta.data[0].get("is_public", False))

                    supabase.table("recept").insert({
                        "user_id": st.session_state["user"].id,
                        "title": norm["title"],
                        "category": norm["category"],
                        "portioner": int(norm["portioner"]),
                        "portioner_enhet": norm["portioner_enhet"],
                        "ingredients": norm["ingredients"],
                        "instructions": norm["instructions"],
                        "nutrition_json": norm["nutrition_json"],
                        "image_url": None,
                        "is_public": current_is_public,
                        "book_name": current_book_name
                    }).execute()
                    st.toast("Receptet har sparats!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Kunde inte spara receptet: {e}")

            def rita_ai_kort(rd, idx):
                norm = normalisera(rd)
                with ai_plats.expander(f"🤖 {norm['title'] or 'Förslag'} — {norm['category'] or 'Middag'}", expanded=False):
                    rendera_receptvisning(norm, f"sok_ai_{idx}", kompakt=True)
                    if st.button("💾 Spara till Mina recept", key=f"spara_ai_{idx}"):
                        spara_ai(norm)

            def ett_anrop():
                """Gör ett anrop, ritar korten direkt och returnerar antal nya objekt."""
                befintliga_titlar = [f.get("title", "") for f in state["ai_forslag"]]
                prompt = (
                    f"Generera goda recept som matchar följande ingredienser: {ingrediens_input}. "
                    f"Undvik dessa redan föreslagna titlar: {', '.join(befintliga_titlar) if befintliga_titlar else 'inga'}. "
                    "Returnera ett recept i taget som enskilda JSON-objekt, ETT per rad, utan array-klamrar och utan mellanslag mellan objekten. "
                    "Varje objekt ska ha nycklarna:\n"
                    "- 'title': Receptets namn\n"
                    "- 'category': Kategori (t.ex. Förrätt, Huvudrätt, Efterrätt, Bakverk)\n"
                    "- 'portioner_antal': Antal portioner som heltal\n"
                    "- 'portioner_enhet': Enhet (t.ex. portioner)\n"
                    "- 'ingredients': Ingredienser på egen rad, med bindestreck i början\n"
                    "- 'instructions': Tillagningssteg utan numrering\n"
                    "- 'nutrition': Objekt med 'per_portion' och 'per_100g': 'energi', 'protein', 'kolhydrater', 'socker', 'fett', 'mattat_fett', 'fiber', 'salt', 'c_vitamin', 'd_vitamin', 'jarn'"
                )
                buf = {"rad": ""}
                start_idx = len(state["ai_forslag"])

                def bearbeta_rad(rad):
                    rad = rad.strip().rstrip(",")
                    if not rad.startswith("{") or not rad.endswith("}"):
                        return
                    try:
                        ny = json.loads(rad)
                    except json.JSONDecodeError:
                        return
                    # hoppa dubletter på titel
                    titlar = {f.get("title", "").lower() for f in state["ai_forslag"]}
                    if str(ny.get("title", "")).lower() in titlar:
                        return
                    state["ai_forslag"].append(ny)
                    rita_ai_kort(ny, start_idx + len(state["ai_forslag"]) - 1)

                for chunk in client.models.generate_content_stream(model=GEMINI_MODEL, contents=prompt):
                    if not chunk.text:
                        continue
                    buf["rad"] += chunk.text
                    while "\n" in buf["rad"]:
                        klar, buf["rad"] = buf["rad"].split("\n", 1)
                        bearbeta_rad(klar)
                if buf["rad"].strip():
                    bearbeta_rad(buf["rad"])
                return len(state["ai_forslag"]) - start_idx

            def fyll_pa(mal):
                """Loopar anrop tills mal antal finns eller modellen slutar ge nytt (max 6 varv)."""
                forvarv = 0
                while len(state["ai_forslag"]) < mal and forvarv < 6:
                    forvarv += 1
                    nya = ett_anrop()
                    if nya == 0:
                        break
                state["har_hamtat"] = True

            if not state["har_hamtat"]:
                with st.spinner("Tar fram receptförslag..."):
                    try:
                        fyll_pa(8)
                    except Exception as e:
                        st.error(f"Kunde inte hämta AI-förslag: {e}")
                if state["ai_forslag"]:
                    st.rerun()
            else:
                for i, rd in enumerate(state["ai_forslag"]):
                    rita_ai_kort(rd, i)

            if state["ai_forslag"]:
                if st.button("➕ Visa fler recept"):
                    with st.spinner("Hämtar fler förslag..."):
                        try:
                            fyll_pa(len(state["ai_forslag"]) + 8)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Kunde inte hämta fler förslag: {e}")
            elif state["har_hamtat"]:
                st.info("Inga AI-förslag hittades för dessa ingredienser.")
