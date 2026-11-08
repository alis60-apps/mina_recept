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

GEMINI_MODEL = "gemini-2.5-flash"

# --- VERSIONSHANTERING ---
VERSION = "v1.60"
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
    rensade = [re.sub(r'^\s*\d+[\.\)\:]\s*', '', r) for r in rader]
    return "\n".join(rensade)

def skala_ingredienser(text, original_servings, target_servings):
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
    return "\n".join([re.sub(pattern, convert_match, rad) for rad in rader])

# --- INLOGGNING ---
if "user" not in st.session_state:
    st.session_state["user"] = None

if not st.session_state["user"]:
    st.subheader("🔑 Logga in")
    with st.form("login_form"):
        email = st.text_input("E-post", key="login_email")
        password = st.text_input("Lösenord", type="password", key="login_password")
        c1, c2 = st.columns(2)
        with c1:
            submit_login = st.form_submit_button("Logga in")
        with c2:
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

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Konto & Inställningar")
    st.write(f"Inloggad som:\n**{st.session_state['user'].email}**")
    st.divider()
    if st.button("Logga ut"):
        st.session_state["user"] = None
        st.rerun()

# --- HUVUDAPPLIKATION (FLIKAR) ---
tab_search, tab_ai, tab_scan = st.tabs(["🔍 Sök i Skafferiet", "🤖 AI-Kock (Nät/Nya recept)", "📸 Skanna Recept"])

# --- FLIK 1: SÖK I SKAFFERIET (EGNA RECEPT) ---
with tab_search:
    st.header("🔍 Sök bland mina recept baserat på ingredienser")
    ing_input = st.text_input("Mata in ingredienser (separera med kommatecken):", placeholder="t.ex. kyckling, ris, vitlök")
    
    if ing_input:
        sokte_ingredienser = [i.strip().lower() for i in ing_input.split(",") if i.strip()]
        res = supabase.table("recept").select("*").eq("user_id", st.session_state["user"].id).execute()
        
        matchade_recept = []
        if res.data:
            for rec in res.data:
                ing_text = (rec.get("ingredients") or "").lower()
                trffar = sum(1 for ing in sokte_ingredienser if ing in ing_text)
                if trffar > 0:
                    matchade_recept.append((trffar, rec))
        
        matchade_recept.sort(key=lambda x: x[0], reverse=True)
        
        if matchade_recept:
            st.success(f"Hittade {len(matchade_recept)} recept som matchar dina ingredienser:")
            for trffar, rec in matchade_recept:
                with st.expander(f"📖 {rec['title']} (Matchar {trffar} av {len(sokte_ingredienser)} ingredienser)"):
                    st.write(f"**Kategori:** {rec.get('category', 'Övrigt')}")
                    st.write(f"**Portioner:** {rec.get('portioner', 4)} {rec.get('portioner_enhet', 'st')}")
                    st.subheader("Ingredienser")
                    st.text(rec.get('ingredients', ''))
                    st.subheader("Instruktioner")
                    st.text(rec.get('instructions', ''))
        else:
            st.info("Hittade inga sparade recept som innehöll dessa ingredienser. Testa AI-kocken i nästa flik!")

# --- FLIK 2: AI-KOCKEN (GENERERA NYA RECEPT FRÅN NÄTET/GEMINI) ---
with tab_ai:
    st.header("🤖 Skapa nytt recept med Gemini AI")
    st.write("Skriv in vad du har i kylen/skafferiet så skapar AI-kocken ett nytt läckert recept åt dig!")
    
    ai_ing = st.text_input("Ingredienser du vill använda:", placeholder="t.ex. köttfärs, krossade tomater, pasta, lök", key="ai_ing_input")
    onskemal = st.text_input("Övriga önskemål (valfritt):", placeholder="t.ex. snabblagat, max 20 min, starkt, vegetariskt")
    
    if st.button("✨ Generera receptförslag", type="primary"):
        if not ai_ing:
            st.warning("Vänligen mata in minst en ingrediens.")
        else:
            with st.spinner("AI-kocken söker inspiration och skapar recept..."):
                prompt = f"""
                Du är en professionell kock. Skapa ett snyggt och välsmakande recept på svenska baserat på följande ingredienser: {ai_ing}.
                Övriga önskemål: {onskemal}.
                
                Svara enbart i giltigt JSON-format med följande struktur utan markdown-formatting runt JSON:
                {{
                  "title": "Receptets namn",
                  "category": "Kategori (t.ex. Middag, Lunch, Bakverk)",
                  "portioner": 4,
                  "portioner_enhet": "portioner",
                  "ingredients": "Ingrediens 1 med mått\\nIngrediens 2 med mått",
                  "instructions": "Steg 1\\nSteg 2"
                }}
                """
                try:
                    response = client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=prompt
                    )
                    clean_res = response.text.strip()
                    if clean_res.startswith("```"):
                        clean_res = re.sub(r"^```(?:json)?\n?", "", clean_res)
                        clean_res = re.sub(r"\n?```$", "", clean_res)
                    
                    data = json.loads(clean_res)
                    st.session_state["generated_recipe"] = data
                    st.success("Nytt recept skapat!")
                except Exception as e:
                    st.error(f"Kunde inte generera recept: {e}")

    if "generated_recipe" in st.session_state:
        g_rec = st.session_state["generated_recipe"]
        st.divider()
        st.subheader(f"🍲 {g_rec.get('title')}")
        st.write(f"**Kategori:** {g_rec.get('category')}")
        st.write(f"**Portioner:** {g_rec.get('portioner')} {g_rec.get('portioner_enhet')}")
        
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("### Ingredienser")
            st.text(formatera_ingredienser(g_rec.get("ingredients")))
        with c2:
            st.markdown("### Instruktioner")
            st.text(ta_bort_numrering(g_rec.get("instructions")))
            
        if st.button("💾 Spara receptet i min Receptbok"):
            try:
                new_entry = {
                    "user_id": st.session_state["user"].id,
                    "title": g_rec.get("title"),
                    "category": g_rec.get("category"),
                    "portioner": g_rec.get("portioner"),
                    "portioner_enhet": g_rec.get("portioner_enhet"),
                    "ingredients": formatera_ingredienser(g_rec.get("ingredients")),
                    "instructions": ta_bort_numrering(g_rec.get("instructions")),
                    "is_public": False
                }
                supabase.table("recept").insert(new_entry).execute()
                st.success(f"'{g_rec.get('title')}' har sparats i din Receptbok!")
                del st.session_state["generated_recipe"]
            except Exception as e:
                st.error(f"Kunde inte spara receptet: {e}")

# --- FLIK 3: SKANNA RECEPT ---
with tab_scan:
    st.header("📸 Skanna recept från bild")
    uploaded_file = st.file_uploader("Välj eller ta en bild på ett recept", type=["jpg", "jpeg", "png"])
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption="Uppladdad bild", use_container_width=True)
        if st.button("Skanna med AI"):
            st.info("Skanningsfunktion redo.")
