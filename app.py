import os
import streamlit as st
import extra_streamlit_components as stx
from supabase import create_client, Client
from google import genai
from PIL import Image

# -----------------------------------------------------------------------------
# 1. PAGE CONFIG & COOKIE MANAGER
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Receptboken",
    page_icon="🍳",
    layout="wide"
)

# Initiera cookie manager för att spara sessionen i webbläsaren

cookie_manager = stx.CookieManager()

# -----------------------------------------------------------------------------
# 2. CLIENT INITIALIZATION (Supabase & Gemini)
# -----------------------------------------------------------------------------
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_supabase()

@st.cache_resource
def init_gemini():
    api_key = st.secrets["GEMINI_API_KEY"]
    return genai.Client(api_key=api_key)

gemini_client = init_gemini()

# -----------------------------------------------------------------------------
# 3. SESSION & COOKIE STATE MANAGEMENT
# -----------------------------------------------------------------------------
# Hämta sparad cookie från webbläsaren om session_state är tom
saved_user = cookie_manager.get(cookie="receptbok_user")

if "user" not in st.session_state:
    if saved_user:
        st.session_state["user"] = saved_user
    else:
        st.session_state["user"] = None

def login(username: str):
    st.session_state["user"] = username
    # Spara cookien i webbläsaren (gäller i 30 dagar)
    cookie_manager.set(
        cookie="receptbok_user",
        val=username,
        key="set_user_cookie"
    )

def logout():
    st.session_state["user"] = None
    cookie_manager.delete(cookie="receptbok_user", key="delete_user_cookie")

# -----------------------------------------------------------------------------
# 4. MAIN APPLICATION
# -----------------------------------------------------------------------------
def main():
    # Om användaren inte är inloggad -> Visa inloggningsvy
    if not st.session_state.get("user"):
        st.title("🍳 Välkommen till Receptboken")
        st.subheader("Logga in för att komma åt dina recept")
        
        with st.form("login_form"):
            username_input = st.text_input("Användarnamn / E-post")
            submit = st.form_submit_button("Logga in")
            
            if submit:
                if username_input.strip():
                    login(username_input.strip())
                    st.success("Inloggad! Laddar om...")
                    st.rerun()
                else:
                    st.error("Vänligen ange ett giltigt användarnamn.")
        return

    # --- INLOGGAD VY ---
    user = st.session_state["user"]
    
    # Sidomeny (Sidebar)
    st.sidebar.title(f"👤 {user}")
    menu_option = st.sidebar.radio(
        "Navigering",
        ["📖 Mina Recept", "📸 Skanna nytt recept (Gemini)", "➕ Lägg till manuellt"]
    )
    
    if st.sidebar.button("🚪 Logga ut"):
        logout()
        st.rerun()

    # -------------------------------------------------------------------------
    # FLIK 1: MINA RECEPT (Hämta från Supabase)
    # -------------------------------------------------------------------------
    if menu_option == "📖 Mina Recept":
        st.title("📖 Mina Recept")
        
        try:
            # Hämta alla recept från Supabase för den inloggade användaren
            response = supabase.table("recept").select("*").order("created_at", desc=True).execute()
            recept_list = response.data

            if not recept_list:
                st.info("Du har inga sparade recept ännu. Använd menyn till vänster för att lägga till eller skanna ett recept!")
            else:
                for recept in recept_list:
                    with st.expander(f"🍲 {recept.get('titel', 'Namnlöst recept')}"):
                        col1, col2 = st.columns([2, 1])
                        with col1:
                            st.write("**Ingredienser:**")
                            st.write(recept.get("ingredienser", "Inga ingredienser angivna."))
                            st.write("**Instruktioner:**")
                            st.write(recept.get("instruktioner", "Inga instruktioner angivna."))
                        with col2:
                            if recept.get("bild_url"):
                                st.image(recept["bild_url"], use_container_width=True)
                            st.caption(f"Kategori: {recept.get('kategori', 'Övrigt')}")

        except Exception as e:
            st.error(f"Kunde inte hämta recept från Supabase: {e}")

    # -------------------------------------------------------------------------
    # FLIK 2: SKANNA NYTT RECEPT (Gemini API)
    # -------------------------------------------------------------------------
    elif menu_option == "📸 Skanna nytt recept (Gemini)":
        st.title("📸 Skanna receptbild med AI")
        st.write("Ladda upp en bild på ett recept från en kokbok eller tidning, så strukturerar Gemini det åt dig.")

        uploaded_file = st.file_uploader("Välj en receptbild...", type=["jpg", "jpeg", "png", "webp"])

        if uploaded_file is not None:
            image = Image.open(uploaded_file)
            st.image(image, caption="Uppladdad bild", use_container_width=True)

            if st.button("✨ Analysera bild med Gemini"):
                with st.spinner("Analyserar bilden och skapar receptet..."):
                    try:
                        prompt = (
                            "Analysera denna receptbild. Extrahera följande information på svenska "
                            "i strukturerad form:\n"
                            "1. Titel på receptet\n"
                            "2. Ingredienser (som en punktlista)\n"
                            "3. Instruktioner / Gör så här (steg för steg)\n"
                            "4. Kategori (t.ex. Middag, Efterrätt, Bakning)"
                        )

                        # Anropa Gemini API
                        response = gemini_client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=[image, prompt]
                        )
                        
                        st.subheader("Resultat från Gemini:")
                        st.markdown(response.text)

                        # Spara i session state för att kunna skicka till Supabase
                        st.session_state["extracted_text"] = response.text

                    except Exception as e:
                        st.error(f"Ett fel uppstod vid analysen: {e}")

        # Spara-knapp om text har extraherats
        if "extracted_text" in st.session_state:
            st.divider()
            if st.button("💾 Spara analyserat recept till Supabase"):
                try:
                    data = {
                        "titel": "Skannat recept",
                        "instruktioner": st.session_state["extracted_text"],
                        "kategori": "Skannat",
                        "anvandare": user
                    }
                    supabase.table("recept").insert(data).execute()
                    st.success("Receptet har sparats i databasen!")
                    del st.session_state["extracted_text"]
                except Exception as e:
                    st.error(f"Kunde inte spara till Supabase: {e}")

    # -------------------------------------------------------------------------
    # FLIK 3: LÄGG TILL MANUELLT
    # -------------------------------------------------------------------------
    elif menu_option == "➕ Lägg till manuellt":
        st.title("➕ Lägg till nytt recept manuellt")

        with st.form("manual_recipe_form"):
            titel = st.text_input("Titel")
            kategori = st.selectbox("Kategori", ["Middag", "Lunch", "Frukost", "Efterrätt", "Bakning", "Övrigt"])
            ingredienser = st.text_area("Ingredienser")
            instruktioner = st.text_area("Instruktioner / Gör så här")
            
            submit_recipe = st.form_submit_button("Spara recept")

            if submit_recipe:
                if titel and ingredienser:
                    try:
                        data = {
                            "titel": titel,
                            "kategori": kategori,
                            "ingredienser": ingredienser,
                            "instruktioner": instruktioner,
                            "anvandare": user
                        }
                        supabase.table("recept").insert(data).execute()
                        st.success(f"Receptet '{titel}' har sparats!")
                    except Exception as e:
                        st.error(f"Kunde inte spara i databasen: {e}")
                else:
                    st.warning("Ange minst titel och ingredienser.")

if __name__ == "__main__":
    main()