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
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

gemini_api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY"))
client = genai.Client(api_key=gemini_api_key)
GEMINI_MODEL = "gemini-3.8-flash"

# --- VERSIONSHANTERING ---
VERSION = "v1.63"
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
        rensad_rad = re.sub(r'^\s*\d+[\.\)\:]\s*', '', r)
        rensade.append(rensad_rad)
    return "\n".join(rensade)

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
tab1, tab2, tab3, tab4 = st.tabs(["📚 Mina Recept", "🔍 Sök Recept", "📸 Skanna Recept", "🤖 Generera med AI"])

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

# --- FLIK 2: SÖK RECEPT (Sök bland ingredienser, namnlista, klicka för att läsa och spara) ---
with tab2:
    st.header("🔍 Sök Recept (Inkl. dina och nya förslag)")
    st.write("Skriv in ingredienser du vill söka på för att hitta matchande recept.")
    
    ingrediens_input = st.text_input("Ingredienser (separera med kommatecken, t.ex. kyckling, ris):", key="sok_ingrediens_input")
    
    if ingrediens_input:
        sokte_ingredienser = [i.strip().lower() for i in ingrediens_input.split(",") if i.strip()]
        
        try:
            # Hämta användarens egna recept
            res = supabase.table("recept").select("*").eq("user_id", st.session_state["user"].id).execute()
            egna_recept = res.data or []
            
            matchade_recept = []
            
            # 1. Kolla egna recept först
            for r in egna_recept:
                sok_text = (r.get("title", "") + " " + r.get("ingredients", "")).lower()
                matches = sum(1 for ing in sokte_ingredienser if ing in sok_text)
                if matches > 0:
                    matchade_recept.append({"source": "egna", "data": r, "score": matches})
            
            # Sortera träffar efter relevans
            matchade_recept.sort(key=lambda x: x["score"], reverse=True)
            
            # Om vi vill ha fler externa/AI-förslag om det behövs eller direkt via sökning
            with st.spinner("Sök bland recept..."):
                prompt = f"""
                Generera 3 till 5 goda recept som matchar följande ingredienser: {ingrediens_input}.
                Returnera svaret som en giltig JSON-lista där varje objekt har:
                - "title": Receptets namn
                - "category": Kategori (t.ex. Middag, Lunch, Övrigt)
                - "portioner": Antal portioner (tal)
                - "portioner_enhet": Enhet (t.ex. portioner)
                - "ingredients": Ingredienser på en rad var
                - "instructions": Instruktioner utan numrering
                """
                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt
                )
                clean_json = re.sub(r'```json\s*|\s*```', '', response.text).strip()
                ai_forslag = json.loads(clean_json)
                
                for af in ai_forslag:
                    matchade_recept.append({"source": "ai", "data": af, "score": 1})
            
            if matchade_recept:
                st.subheader("Matchande recept (Klicka på ett namn för att läsa):")
                
                for idx, item in enumerate(matchade_recept):
                    rec_data = item["data"]
                    titel_prefix = "⭐ [Ditt sparade] " if item["source"] == "egna" else "🤖 [AI-förslag] "
                    
                    with st.expander(f"{titel_prefix}{rec_data.get('title')} ({rec_data.get('category', 'Middag')})"):
                        st.write(f"**Portioner:** {rec_data.get('portioner', 4)} {rec_data.get('portioner_enhet', 'portioner')}")
                        st.subheader("Ingredienser")
                        st.text(formatera_ingredienser(rec_data.get("ingredients", "")))
                        st.subheader("Instruktioner")
                        st.text(ta_bort_numrering(rec_data.get("instructions", "")))
                        
                        # Om det är ett AI-förslag som inte är sparat, visa spara-knapp
                        if item["source"] == "ai":
                            if st.button("💾 Spara till Mina Recept", key=f"save_ai_{idx}"):
                                try:
                                    supabase.table("recept").insert({
                                        "user_id": st.session_state["user"].id,
                                        "title": rec_data.get("title"),
                                        "category": rec_data.get("category"),
                                        "portioner": rec_data.get("portioner"),
                                        "portioner_enhet": rec_data.get("portioner_enhet"),
                                        "ingredients": rec_data.get("ingredients"),
                                        "instructions": rec_data.get("instructions")
                                    }).execute()
                                    st.success("Receptet har sparats till dina recept!")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Kunde inte spara receptet: {e}")
            else:
                st.info("Inga recept hittades för dessa ingredienser.")
                
        except Exception as e:
            st.error(f>Ett fel uppstod vid sökning: {e}")

# --- FLIK 3: SKANNA RECEPT ---
with tab3:
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
                    - "portioner_enhet": Enhet (t.ex. portioner, st)
                    - "ingredients": En sträng med varje ingrediens på ny rad.
                    - "instructions": Steg-för-steg instruktioner utan numrering.
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

# --- FLIK 4: GENERERA MED AI ---
with tab4:
    st.header("🤖 Generera nya recept med AI")
    ai_ingredienser = st.text_area("Vilka ingredienser har du hemma?", placeholder="T.ex. lax, spenat, grädde", height=100)
    onskemal = st.text_input("Övriga önskemål:", placeholder="T.ex. snabblagat under 20 min")
    
    if st.button("✨ Generera nytt receptförslag"):
        if not ai_ingredienser:
            st.warning("Skriv in minst en ingrediens!")
        else:
            with st.spinner("Gemini komponerar ett recept..."):
                try:
                    prompt = f"""
                    Generera ett välsmakande recept baserat på:
                    Ingredienser: {ai_ingredienser}
                    Önskemål: {onskemal}

                    Returnera i EXAKT JSON-format med nycklarna:
                    - "title": Receptets namn
                    - "category": Kategori (Middag, Lunch, Frukost, Efterrätt, Bakning, Övrigt)
                    - "portioner": Antal portioner som ett numeriskt tal
                    - "portioner_enhet": Enhet (t.ex. portioner)
                    - "ingredients": Ingredienser på en rad var
                    - "instructions": Instruktioner utan numrering
                    """
                    response = client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=prompt
                    )
                    clean_json = re.sub(r'```json\s*|\s*```', '', response.text).strip()
                    generated_recipe = json.loads(clean_json)
                    st.session_state["generated_recipe"] = generated_recipe
                    st.success("Recept genererat!")
                except Exception as e:
                    st.error(f"Kunde inte generera recept: {e}")

    if "generated_recipe" in st.session_state:
        gen = st.session_state["generated_recipe"]
        st.divider()
        st.subheader(f"🍽️ Förslag: {gen.get('title')}")
        st.write(f"**Kategori:** {gen.get('category')} | **Portioner:** {gen.get('portioner')} {gen.get('portioner_enhet')}")
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
                st.success("Sparat till ditt bibliotek!")
                del st.session_state["generated_recipe"]
                st.rerun()
            except Exception as e:
                st.error(f"Kunde inte spara: {e}")
