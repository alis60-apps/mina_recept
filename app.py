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
VERSION = "v1.61"
st.title(f"📖 Receptboken ({VERSION})")

# --- HJÄLPER-FUNKTIONER FÖR FORMATERING ---
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
        rensad_rad = re.sub(r'^\s*\d+[\.\)\:]\s*', '', r)
        rensade.append(rensad_rad)
    return "\n".join(rensade)

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

    if st.button("Logga ut"):
        supabase.auth.sign_out()
        st.session_state["user"] = None
        st.rerun()

# --- HOVUDMENY / FLIKAR ---
tab1, tab2, tab3, tab4 = st.tabs(["📚 Mina Recept", "📸 Skanna Recept", "🔍 Sök på Ingredienser", "🤖 Generera med AI"])

# --- FLIK 1: MINA RECEPT ---
with tab1:
    st.header("📚 Dina Sparade Recept")
    try:
        res = supabase.table("recept").select("*").eq("user_id", st.session_state["user"].id).execute()
        recept_lista = res.data or []
        
        if not recept_lista:
            st.info("Du har inga sparade recept ännu. Använd AI-skannern eller AI-kocken för att lägga till nya!")
        else:
            sokord = st.text_input("🔍 Sök i titlar eller ingredienser:", key="recept_sok")
            if sokord:
                recept_lista = [r for r in recept_lista if sokord.lower() in r.get("title", "").lower() or sokord.lower() in r.get("ingredients", "").lower()]

            for r in recept_lista:
                with st.expander(f"🍽️ {r.get('title', 'Namnlöst recept')} ({r.get('category', 'Övrigt')})"):
                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.write(f"**Portioner:** {r.get('portioner', 4)} {r.get('portioner_enhet', 'portioner')}")
                    with col_b:
                        st.write(f"**Kategori:** {r.get('category', 'Allmänt')}")
                    
                    st.subheader("Ingredienser")
                    st.text(formatera_ingredienser(r.get("ingredients", "")))
                    
                    st.subheader("Instruktioner")
                    st.text(ta_bort_numrering(r.get("instructions", "")))
                    
                    if st.button("🗑️ Ta bort recept", key=f"del_{r.get('id')}"):
                        supabase.table("recept").delete().eq("id", r.get("id")).execute()
                        st.success("Receptet borttaget!")
                        st.rerun()
    except Exception as e:
        st.error(f"Kunde inte hämta recept: {e}")

# --- FLIK 2: SKANNA RECEPT ---
with tab2:
    st.header("📸 Skanna receptbild")
    uploaded_file = st.file_uploader("Välj eller ta en bild på ett recept", type=["jpg", "jpeg", "png"])
    
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption="Uppladdad bild", use_container_width=True)
        
        if st.button("🪄 Tolka med Gemini AI"):
            with st.spinner("Analyserar bilden..."):
                try:
                    prompt = """
                    Tolka detta recept från bilden och returnera ett JSON-objekt med exakt dessa nycklar:
                    - "title": Receptets titel
                    - "category": Kategori (t.ex. Middag, Efterrätt, Bakning)
                    - "portioner": Antal portioner som ett tal
                    - "portioner_enhet": Enhet (t.ex. portioner, st, kakor, plåtar)
                    - "ingredients": En sträng där varje ingrediens är på en ny rad med mängd, enhet och namn.
                    - "instructions": En ren textsträng med steg-för-steg instruktioner utan numrering.
                    """
                    response = client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=[prompt, image]
                    )
                    clean_text = re.sub(r'```json\s*|\s*```', '', response.text).strip()
                    data = json.loads(clean_text)
                    st.session_state["scanned_recipe"] = data
                    st.success("Receptet tolkades framgångsrikt!")
                except Exception as e:
                    st.error(f"Kunde inte tolka bilden: {e}")

    if "scanned_recipe" in st.session_state:
        sr = st.session_state["scanned_recipe"]
        st.subheader("Granska & Spara")
        with st.form("save_scanned_form"):
            title = st.text_input("Titel", value=sr.get("title", ""))
            category = st.selectbox("Kategori", ["Middag", "Lunch", "Frukost", "Efterrätt", "Bakning", "Övrigt"], index=0)
            col1, col2 = st.columns(2)
            with col1:
                portioner = st.number_input("Portioner", value=float(sr.get("portioner", 4)))
            with col2:
                portioner_enhet = st.text_input("Enhet", value=sr.get("portioner_enhet", "portioner"))
            
            ingredients = st.text_area("Ingredienser", value=sr.get("ingredients", ""), height=150)
            instructions = st.text_area("Instruktioner", value=sr.get("instructions", ""), height=150)
            
            if st.form_submit_button("💾 Spara till Mina Recept"):
                try:
                    supabase.table("recept").insert({
                        "user_id": st.session_state["user"].id,
                        "title": title,
                        "category": category,
                        "portioner": portioner,
                        "portioner_enhet": portioner_enhet,
                        "ingredients": ingredients,
                        "instructions": instructions
                    }).execute()
                    st.success("Receptet har sparats!")
                    del st.session_state["scanned_recipe"]
                    st.rerun()
                except Exception as e:
                    st.error(f"Kunde inte spara receptet: {e}")

# --- FLIK 3: SÖK PÅ INGREDIENSER ---
with tab3:
    st.header("🔍 Sök i skafferiet")
    st.write("Skriv in de ingredienser du har hemma för att hitta matchande recept från din egen receptbok.")
    
    ingrediens_input = st.text_input("Ingredienser (separera med kommatecken, t.ex. kyckling, ris, lök):", key="search_ing_input")
    
    if ingrediens_input:
        sokte_ingredienser = [i.strip().lower() for i in ingrediens_input.split(",") if i.strip()]
        
        try:
            res = supabase.table("recept").select("*").eq("user_id", st.session_state["user"].id).execute()
            alla_recept = res.data or []
            
            matchade_recept = []
            for r in alla_recept:
                ing_text = r.get("ingredients", "").lower()
                matches = sum(1 for ing in sokte_ingredienser if ing in ing_text)
                if matches > 0:
                    matchade_recept.append((r, matches))
            
            matchade_recept.sort(key=lambda x: x[1], reverse=True)
            
            if matchade_recept:
                st.subheader(f"Hittade {len(matchade_recept)} recept som matchar dina ingredienser:")
                for r, score in matchade_recept:
                    with st.expander(f"✨ {r.get('title')} (Matchar {score} av {len(sokte_ingredienser)} ingredienser)"):
                        st.write(f"**Kategori:** {r.get('category', 'Allmänt')}")
                        st.subheader("Ingredienser")
                        st.text(formatera_ingredienser(r.get("ingredients", "")))
                        st.subheader("Instruktioner")
                        st.text(ta_bort_numrering(r.get("instructions", "")))
            else:
                st.info("Inga sparade recept matchade alla/någon av dina inmatade ingredienser. Testa fliken '🤖 Generera med AI' för att skapa ett helt nytt recept på nätet!")
        except Exception as e:
            st.error(f"Kunde inte söka i recepten: {e}")

# --- FLIK 4: GENERERA MED AI ---
with tab4:
    st.header("🤖 Generera nya receptförslag från nätet / AI")
    st.write("Få inspiration och nya recept skapade utifrån vad du har i kylen eller vad du är sugen på!")
    
    ai_ingredienser = st.text_area("Vilka ingredienser har du hemma?", placeholder="T.ex. lax, spenat, citronskal, grädde", height=100)
    onskemal = st.text_input("Övriga önskemål (t.ex. snabblagat, lågkolhydrat, festligt):", placeholder="Snabblagat under 20 min")
    
    if st.button("✨ Generera nytt receptförslag"):
        if not ai_ingredienser:
            st.warning("Skriv in minst en ingrediens!")
        else:
            with st.spinner("Gemini söker inspiration på nätet och komponerar receptet..."):
                try:
                    prompt = f"""
                    Du är en professionell kock. Generera ett välsmakande recept baserat på följande ingredienser och önskemål:
                    Ingredienser tillgängliga: {ai_ingredienser}
                    Önskemål: {onskemal}

                    Returnera svaret i EXAKT JSON-format med följande nycklar:
                    - "title": Receptets namn
                    - "category": Kategori (Middag, Lunch, Frukost, Efterrätt, Bakning eller Övrigt)
                    - "portioner": Antal portioner som ett numeriskt tal (t.ex. 4)
                    - "portioner_enhet": Enhet (t.ex. portioner, st)
                    - "ingredients": En sträng med alla ingredienser och mängder, en ingrediens per rad.
                    - "instructions": Steg-för-steg instruktioner för tillagning utan numrering.
                    """
                    
                    response = client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=prompt
                    )
                    
                    clean_json = re.sub(r'```json\s*|\s*```', '', response.text).strip()
                    generated_recipe = json.loads(clean_json)
                    st.session_state["generated_recipe"] = generated_recipe
                    st.success("Nytt recept genererat!")
                except Exception as e:
                    st.error(f"Kunde inte generera recept: {e}")

    if "generated_recipe" in st.session_state:
        gen = st.session_state["generated_recipe"]
        st.divider()
        st.subheader(f"🍽️ Förslag: {gen.get('title')}")
        
        col_g1, col_g2 = st.columns(2)
        with col_g1:
            st.write(f"**Kategori:** {gen.get('category')}")
        with col_g2:
            st.write(f"**Portioner:** {gen.get('portioner')} {gen.get('portioner_enhet')}")
            
        st.subheader("Ingredienser")
        st.text(formatera_ingredienser(gen.get("ingredients", "")))
        
        st.subheader("Instruktioner")
        st.text(ta_bort_numrering(gen.get("instructions", "")))
        
        if st.button("💾 Spara detta recept till Mina Recept"):
            try:
                supabase.table("recept").insert({
                    "user_id": st.session_state["user"].id,
                    "title": gen.get("title"),
                    "category": gen.get("category"),
                    "portioner": gen.get("portioner"),
                    "portioner_enhet": gen.get("portioner_enhet"),
                    "ingredients": gen.get("ingredients"),
                    "instructions": gen.get("instructions")
                }).execute()
                st.success("Receptet sparades till ditt receptbibliotek i Supabase!")
                del st.session_state["generated_recipe"]
                st.rerun()
            except Exception as e:
                st.error(f"Kunde inte spara receptet: {e}")
