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

# --- KEEP-ALIVE ---
components.html(
    """
    <script>
    setInterval(function() {
        window.dispatchEvent(new Event('resize'));
    }, 240000);
    </script>
    """,
    height=0
)

# --- SUPABASE & GEMINI CONFIG ---
SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.environ.get("SUPABASE_URL"))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", os.environ.get("SUPABASE_KEY"))
gemini_api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY"))

_saknade = []
if not SUPABASE_URL:
    _saknade.append("SUPABASE_URL")
if not SUPABASE_KEY:
    _saknade.append("SUPABASE_KEY")
if not gemini_api_key:
    _saknade.append("GEMINI_API_KEY")
if _saknade:
    st.error(f"Saknade konfigurationsvärden: {', '.join(_saknade)}. Lägg till dem i secrets/omgivningsvariabler.")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = genai.Client(api_key=gemini_api_key)
GEMINI_MODEL = "gemini-3.8-flash"

# --- VERSIONSHANTERING ---
VERSION = "v1.65"
st.title(f"📖 Receptboken ({VERSION})")

# --- HJÄLPFUNKTIONER ---
def formatera_ingredienser(text):
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
    if not text:
        return ""
    rader = str(text).split("\n")
    rensade = []
    for r in rader:
        rensad_rad = re.sub(r'^\s*\d+[\.$$\:]\s*', '', r)
        rensade.append(rensad_rad)
    return "\n".join(rensade)

def till_int(varde, default=4):
    try:
        return int(float(varde))
    except (TypeError, ValueError):
        return default

def kategori_index(vald, lista):
    try:
        return lista.index(vald)
    except (ValueError, AttributeError):
        return 0

def rendera_receptkort(rec_data, nyckel, spar_knapp=False):
    """Delad uppställning för receptkort i Mina recept och Sök recept."""
    st.write(f"**Portioner:** {rec_data.get('portioner', 4)} {rec_data.get('portioner_enhet', 'portioner')}")
    st.write(f"**Kategori:** {rec_data.get('category', 'Allmänt')}")
    st.subheader("Ingredienser")
    st.text(formatera_ingredienser(rec_data.get("ingredients", "")))
    st.subheader("Instruktioner")
    st.text(ta_bort_numrering(rec_data.get("instructions", "")))
    if spar_knapp:
        if st.button("💾 Spara till Mina Recept", key=f"save_{nyckel}"):
            try:
                supabase.table("recept").insert({
                    "user_id": st.session_state["user"].id,
                    "title": rec_data.get("title"),
                    "category": rec_data.get("category"),
                    "portioner": till_int(rec_data.get("portioner")),
                    "portioner_enhet": rec_data.get("portioner_enhet"),
                    "ingredients": rec_data.get("ingredients"),
                    "instructions": rec_data.get("instructions")
                }).execute()
                st.success("Receptet har sparats till dina recept!")
                st.rerun()
            except Exception as e:
                st.error(f"Kunde inte spara receptet: {e}")

# --- INLOGGNING ---
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
                st.info("Konto skapat! Kolla din inkorg om bekräftelse krävs.")
            except Exception as e:
                st.error(f"Kunde inte skapa konto: {e}")
    st.stop()

# --- SIDOMENY ---
with st.sidebar:
    st.header("⚙️ Konto & Inställningar")
    st.write(f"Inloggad som:\n**{st.session_state['user'].email}**")
    st.divider()

    if st.button("Logga ut"):
        supabase.auth.sign_out()
        st.session_state["user"] = None
        st.rerun()

# --- FLIKAR ---
tab1, tab2, tab3 = st.tabs(["📚 Mina Recept", "🔍 Sök Recept", "📸 Skanna Recept"])

# --- FLIK 1: MINA RECEPT ---
with tab1:
    st.header("📚 Dina Sparade Recept")
    try:
        res = supabase.table("recept").select("*").eq("user_id", st.session_state["user"].id).execute()
        recept_lista = res.data or []

        if not recept_lista:
            st.info("Du har inga sparade recept ännu.")
        else:
            sokord = st.text_input("🔍 Sök i dina sparade recept:", key="mina_recept_sok")
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

# --- FLIK 2: SÖK RECEPT ---
with tab2:
    st.header("🔍 Sök Recept")
    st.caption("Sök bland dina sparade recept och få nya AI-förslag baserat på ingredienser.")

    ingrediens_input = st.text_input("Ingredienser (separera med kommatecken, t.ex. kyckling, ris):", key="sok_ingrediens_input")

    if "sok_state" not in st.session_state:
        st.session_state["sok_state"] = None

    if ingrediens_input:
        # Ny sökning om ingredienserna ändrats
        if st.session_state["sok_state"] is None or st.session_state["sok_state"].get("input") != ingrediens_input:
            st.session_state["sok_state"] = {"input": ingrediens_input, "ai_forslag": [], "har_hamtat": False}

    if ingrediens_input and st.session_state["sok_state"] is not None:
        state = st.session_state["sok_state"]
        sokte_ingredienser = [i.strip().lower() for i in ingrediens_input.split(",") if i.strip()]

        # Egna träffar
        try:
            res = supabase.table("recept").select("*").eq("user_id", st.session_state["user"].id).execute()
            egna_recept = res.data or []
            egna_traffar = []
            for r in egna_recept:
                sok_text = (r.get("title", "") + " " + r.get("ingredients", "")).lower()
                matches = sum(1 for ing in sokte_ingredienser if ing in sok_text)
                if matches > 0:
                    egna_traffar.append((matches, r))
            egna_traffar.sort(key=lambda x: x[0], reverse=True)
        except Exception as e:
            st.error(f"Kunde inte hämta recept: {e}")
            egna_traffar = []

        def hamta_ai_forslag():
            prompt = f"""
            Generera 3 till 5 goda recept som matchar följande ingredienser: {ingrediens_input}.
            Undvik dessa redan föreslagna titlar: {', '.join([f.get('title', '') for f in state['ai_forslag']]) or 'inga'}.
            Returnera svaret som en giltig JSON-lista där varje objekt har:
            - "title": Receptets namn
            - "category": Kategori (t.ex. Middag, Lunch, Övrigt)
            - "portioner": Antal portioner (tal)
            - "portioner_enhet": Enhet (t.ex. portioner)
            - "ingredients": Ingredienser på en rad var
            - "instructions": Instruktioner utan numrering
            """
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            clean_json = re.sub(r'```json\s*|\s*```', '', response.text).strip()
            nya = json.loads(clean_json)
            state["ai_forslag"].extend(nya)
            state["har_hamtat"] = True

        # Hämta första omgången automatiskt
        if not state["har_hamtat"]:
            with st.spinner("Söker bland dina recept och tar fram förslag..."):
                try:
                    hamta_ai_forslag()
                except Exception as e:
                    st.error(f"Kunde inte h