import os
import json
import uuid
import re
import html
import pandas as pd
from PIL import Image, ImageOps
import streamlit as st
import streamlit.components.v1 as components
from supabase import create_client, Client
from google import genai
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(page_title="Receptboken", page_icon="📖", layout="centered")

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

SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.environ.get("SUPABASE_URL"))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", os.environ.get("SUPABASE_KEY"))
SUPABASE_SERVICE_KEY = st.secrets.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_SERVICE_KEY"))
ADMIN_EMAIL = st.secrets.get("ADMIN_EMAIL", "")
BUCKET = "recept-bilder"
CONTENT_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}

gemini_api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY"))
client = genai.Client(api_key=gemini_api_key)

GEMINI_MODEL = "gemini-3.8-flash"

VERSION = "v1.85"


def skapa_supabase() -> Client:
    """Ny klient per körning, men auth-sessionen återställs så att RLS och utloggning fungerar."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    sparad = st.session_state.get("auth_session")
    if sparad:
        try:
            sb.auth.set_session(sparad["access_token"], sparad["refresh_token"])
        except Exception:
            st.session_state["auth_session"] = None
            st.session_state["user"] = None
    return sb


supabase: Client = skapa_supabase()
admin_supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else supabase

st.title(f"📖 Receptboken med AI-skanning ({VERSION})")


def till_int(varde, standard=4, minimum=1, maximum=500):
    """Tål None, strängar, '4 portioner', '4,0' och flyttal."""
    if varde is None:
        return standard
    try:
        text = str(varde).replace(",", ".").strip().split()[0]
        tal = int(float(text))
    except (ValueError, IndexError):
        return standard
    return max(minimum, min(maximum, tal))


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
    rensade = [re.sub(r'^\s*(?:steg\s*)?\d+[.:)\-]\s*', '', r, flags=re.IGNORECASE) for r in rader]
    return "\n".join(rensade)


TAL_MONSTER = re.compile(
    r'(?<![\w.,])(\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d+[.,]\d+|\d+)'
    r'(?![\w,]|\s*(?:°|grader|graderna))'
)


def _tolka_tal(text):
    text = text.strip()
    m = re.fullmatch(r'(\d+)\s+(\d+)\s*/\s*(\d+)', text)
    if m:
        hel, taljare, namnare = m.groups()
        try:
            return int(hel) + int(taljare) / int(namnare)
        except ZeroDivisionError:
            return float(hel)
    m = re.fullmatch(r'(\d+)\s*/\s*(\d+)', text)
    if m:
        taljare, namnare = m.groups()
        try:
            return int(taljare) / int(namnare)
        except ZeroDivisionError:
            return 0.0
    return float(text.replace(",", "."))


def _formatera_tal(tal):
    if abs(tal - round(tal)) < 1e-9:
        return str(int(round(tal)))
    return f"{tal:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def skala_ingredienser(text, original_servings, target_servings):
    original_servings = till_int(original_servings, 4)
    target_servings = till_int(target_servings, original_servings)
    if not text or original_servings <= 0 or original_servings == target_servings:
        return text

    factor = target_servings / original_servings

    def ersatt(match):
        try:
            tal = _tolka_tal(match.group(1))
        except (ValueError, ZeroDivisionError):
            return match.group(0)
        return _formatera_tal(tal * factor)

    rader = str(text).split("\n")
    return "\n".join(TAL_MONSTER.sub(ersatt, rad) for rad in rader)


def bygg_html_lista(text, med_punkt=True):
    rader = [r.strip() for r in str(text or "").split("\n") if r.strip()]
    if not rader:
        return ""
    punkter = []
    for r in rader:
        r = re.sub(r'^[-•*]\s*', '', r)
        punkter.append(f"<li>{html.escape(r)}</li>")
    tag = "ul" if med_punkt else "div"
    stil = "margin:0;padding-left:18px;line-height:1.3;" if med_punkt else "line-height:1.35;"
    return f"<{tag} style=\"{stil}\">" + "".join(punkter) + f"</{tag}>"


def extrahera_json(text):
    """Plockar ut JSON även om modellen lindar in det i kodblock eller text."""
    text = str(text).strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*```$', '', text)
    start, slut = text.find("{"), text.rfind("}")
    if start != -1 and slut > start:
        text = text[start:slut + 1]
    return json.loads(text)


NARINGS_ETIKETTER = [
    ("⚡ Energi", "energi"), ("🥩 Protein", "protein"), ("🍞 Kolhydrater", "kolhydrater"),
    ("varav sockerarter", "socker"), ("🥑 Fett", "fett"), ("varav mättat fett", "mattat_fett"),
    ("🌾 Fiber", "fiber"), ("🧂 Salt", "salt"), ("🍊 C-vitamin", "c_vitamin"),
    ("☀️ D-vitamin", "d_vitamin"), ("🧪 Järn", "jarn"),
]


def _naring_ramar(nutrition):
    if not isinstance(nutrition, dict):
        return {}, {}
    per_portion = nutrition.get("per_portion") or {}
    per_100g = nutrition.get("per_100g") or {}
    return (
        per_portion if isinstance(per_portion, dict) else {},
        per_100g if isinstance(per_100g, dict) else {},
    )


def bygg_naringstabell(nutrition):
    p_data, h_data = _naring_ramar(nutrition)
    if not p_data and not h_data:
        return None
    return pd.DataFrame({
        "Näringsämne": [lbl for lbl, _ in NARINGS_ETIKETTER],
        "Per 100 g": [h_data.get(ky, "-") for _, ky in NARINGS_ETIKETTER],
        "Per Portion": [p_data.get(ky, "-") for _, ky in NARINGS_ETIKETTER],
    })


def rendera_receptvisning(rec_data, unik_nyckel, ta_med_naring=True, kompakt=False):
    titel = rec_data.get("title") or rec_data.get("titel") or "Namnlöst recept"
    kategori = rec_data.get("category") or rec_data.get("kategori") or "Övrigt"
    ingredients = rec_data.get("ingredients") or ""
    instructions = rec_data.get("instructions") or ""
    orig_portioner = till_int(rec_data.get("portioner") or rec_data.get("servings"), 4)
    orig_enhet = rec_data.get("portioner_enhet") or "portioner"
    image_url = rec_data.get("image_url")
    nutrition_dict = rec_data.get("nutrition_json") or {}

    if not ingredients and not instructions and rec_data.get("text"):
        ingredients = rec_data.get("text")

    if image_url:
        st.image(image_url, caption="Skannat recept", width=160 if kompakt else 300)

    col_p1, _ = st.columns([1, 2])
    with col_p1:
        valda_portioner = st.number_input(
            f"Antal ({orig_enhet}):",
            min_value=1, max_value=500, value=orig_portioner, step=1,
            key=f"portioner_{unik_nyckel}"
        )
    if valda_portioner != orig_portioner:
        st.caption(f"ℹ️ Omräknat från {orig_portioner} {orig_enhet} till {valda_portioner} {orig_enhet}.")

    skalad_ing = skala_ingredienser(ingredients, orig_portioner, valda_portioner)
    rensad_inst = ta_bort_numrering(instructions)

    if kompakt:
        st.caption("🛒 Ingredienser")
        st.markdown(bygg_html_lista(formatera_ingredienser(skalad_ing), med_punkt=True), unsafe_allow_html=True)
        st.caption("👨‍🍳 Tillagning")
        st.markdown(bygg_html_lista(rensad_inst, med_punkt=False), unsafe_allow_html=True)
    else:
        st.markdown("### 🛒 Ingredienser")
        st.markdown(formatera_ingredienser(skalad_ing))
        st.markdown("### 👨‍🍳 Tillagning")
        st.markdown(rensad_inst)

    if ta_med_naring:
        df_naring_vis = bygg_naringstabell(nutrition_dict)
        if df_naring_vis is not None:
            if kompakt:
                st.caption("📊 Näringsinnehåll & Vitaminer")
            else:
                st.markdown("### 📊 Näringsinnehåll & Vitaminer")
            st.table(df_naring_vis)

    return titel, kategori


def bygg_utskriftsdokument(titel, kategori, portion_text, ingredienser, instruktioner, naring_html):
    def esc(varde):
        return html.escape(str(varde)).replace("\n", "<br>")

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{esc(titel)}</title>
<style>
body {{ font-family: sans-serif; padding: 30px; color: #111; line-height: 1.5; }}
h2 {{ margin-bottom: 5px; }}
p {{ color: #555; }}
hr {{ border: 0; border-top: 1px solid #ddd; margin: 20px 0; }}
h3 {{ margin-top: 20px; font-size: 16px; color: #222; border-bottom: 1px solid #ccc; padding-bottom: 5px; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }}
th, td {{ border-bottom: 1px solid #eee; padding: 6px; text-align: left; }}
th {{ background-color: #f9f9f9; }}
</style></head>
<body>
<h2>{esc(titel)}</h2>
<p><em>Kategori: {esc(kategori)}</em></p>
<hr/>
<h3>🛒 Ingredienser ({esc(portion_text)})</h3><p>{esc(ingredienser)}</p>
<h3>👨‍🍳 Tillagning</h3><p>{esc(instruktioner)}</p>
{naring_html}
</body></html>"""


if "user" not in st.session_state:
    st.session_state["user"] = None
if "auth_session" not in st.session_state:
    st.session_state["auth_session"] = None

if not st.session_state["user"] or not st.session_state["auth_session"]:
    st.session_state["user"] = None
    st.subheader("🔑 Logga in")

    email = st.text_input("E-post", key="login_email", autocomplete="username")

    adress_ren = (email or "").strip().lower()
    adress_giltig = "@" in adress_ren and "." in adress_ren
    adress_tillaten = False
    adress_har_konto = False

    if adress_giltig:
        try:
            adress_tillaten = bool(
                supabase.table("konto_forfragningar").select("id").eq(
                    "email", adress_ren
                ).eq("status", "tillaten").execute().data
            )
        except Exception:
            adress_tillaten = False
        if adress_tillaten:
            try:
                for u in admin_supabase.auth.admin.list_users():
                    if (u.email or "").lower() == adress_ren:
                        adress_har_konto = True
                        break
            except Exception:
                pass
        if adress_ren and adress_ren == ADMIN_EMAIL.lower():
            adress_tillaten = True
        if not adress_tillaten:
            st.caption("Adressen är inte godkänd än. Begär konto nedan.")
        elif adress_har_konto:
            st.caption("Adressen har redan ett konto – logga in.")
        else:
            st.caption("Adressen är godkänd. Välj ett lösenord och skapa kontot.")

    with st.form("login_form"):
        password = st.text_input("Lösenord", type="password", key="login_password", autocomplete="current-password")

        col1, col2 = st.columns(2)
        with col1:
            submit_login = st.form_submit_button("Logga in", disabled=not adress_giltig)
        with col2:
            submit_signup = st.form_submit_button(
                "Skapa konto",
                disabled=not (adress_giltig and adress_tillaten and not adress_har_konto),
            )

        if submit_login:
            try:
                auth_res = supabase.auth.sign_in_with_password({"email": email, "password": password})
                if auth_res.session is None:
                    st.error("Inloggningen kräver e-postbekräftelse. Kolla din inkorg.")
                else:
                    if not adress_tillaten:
                        try:
                            supabase.auth.sign_out()
                        except Exception:
                            pass
                        st.error("Adressen är inte godkänd för appen. Begär konto nedan.")
                    else:
                        st.session_state["auth_session"] = {
                            "access_token": auth_res.session.access_token,
                            "refresh_token": auth_res.session.refresh_token,
                        }
                        st.session_state["user"] = auth_res.user
                        st.rerun()
            except Exception as e:
                st.error(f"Fel vid inloggning: {e}")

        if submit_signup:
            try:
                supabase.auth.sign_up({"email": email, "password": password})
                st.info("Konto skapat! Bekräfta via mejlet och logga sedan in.")
            except Exception as e:
                st.error(f"Kunde inte skapa konto: {e}")

    st.divider()

    st.subheader("📨 Begär konto")
    with st.form("konto_forfragan"):
        ny_email = st.text_input("E-post för nytt konto", key="ny_email")
        skicka = st.form_submit_button("Skicka förfrågan")
        if skicka:
            if not ny_email or "@" not in ny_email:
                st.error("Ange en giltig e-postadress.")
            else:
                adress = ny_email.strip().lower()
                finns_redan = False
                try:
                    for u in admin_supabase.auth.admin.list_users():
                        if (u.email or "").lower() == adress:
                            finns_redan = True
                            break
                except Exception:
                    pass

                if finns_redan:
                    st.info("Det finns redan ett konto på den adressen. Logga in i stället.")
                else:
                    try:
                        supabase.table("konto_forfragningar").insert({"email": adress}).execute()
                        st.success("Förfrågan skickad. Du får besked när adressen är godkänd.")
                    except Exception as e:
                        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
                            st.info("Det finns redan en förfrågan på den adressen.")
                        else:
                            st.error(f"Kunde inte skicka förfrågan: {e}")

    st.stop()

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

    function visa(lage) {
        const btn = document.getElementById('wakelock-btn');
        const status = document.getElementById('wakelock-status');
        if (lage === 'pa') {
            btn.innerText = '💡 Håll skärmen tänd: PÅ';
            btn.style.backgroundColor = '#ff9800';
            status.innerText = 'Skärmen förblir tänd';
        } else {
            btn.innerText = '💡 Håll skärmen tänd: AV';
            btn.style.backgroundColor = '#4CAF50';
            status.innerText = 'Normal skärmsläckning';
        }
    }

    if (localStorage.getItem('wakelock') === '1') {
        visa('pa');
        document.getElementById('wakelock-status').innerText = 'Tryck för att aktivera igen';
    }

    async function begar() {
        try {
            wakeLock = await navigator.wakeLock.request('screen');
            wakeLock.addEventListener('release', function () { wakeLock = null; });
            return true;
        } catch (err) {
            document.getElementById('wakelock-status').innerText = 'Kunde inte aktivera: ' + err.message;
            return false;
        }
    }

    async function toggleWakeLock() {
        if (!('wakeLock' in navigator)) {
            document.getElementById('wakelock-status').innerText = 'Din webbläsare stöder inte Screen Wake Lock API.';
            return;
        }
        if (wakeLock === null) {
            if (await begar()) {
                localStorage.setItem('wakelock', '1');
                visa('pa');
            }
        } else {
            wakeLock.release();
            wakeLock = null;
            localStorage.setItem('wakelock', '0');
            visa('av');
        }
    }

    document.addEventListener('visibilitychange', async function () {
        if (document.visibilityState === 'visible' && wakeLock === null && localStorage.getItem('wakelock') === '1') {
            await begar();
        }
    });
    </script>
    """
    components.html(wake_lock_html, height=80)

    st.divider()

    st.subheader("Mina delningsinställningar")
    nuvarande_status = False
    nuvarande_book_name = "Min Receptbok"
    try:
        user_check = supabase.table("recept").select("is_public, book_name").eq("user_id", st.session_state["user"].id).limit(1).execute()
        if user_check.data:
            nuvarande_status = bool(user_check.data[0].get("is_public", False))
            nuvarande_book_name = user_check.data[0].get("book_name") or "Min Receptbok"
    except Exception:
        pass

    if "bok_namn_input" not in st.session_state:
        st.session_state["bok_namn_input"] = nuvarande_book_name
    if "bok_publik_input" not in st.session_state:
        st.session_state["bok_publik_input"] = nuvarande_status

    st.text_input("Namn på din receptbok", key="bok_namn_input")
    st.checkbox("🌐 Gör hela receptboken publik", key="bok_publik_input")

    if st.button("Spara inställningar"):
        try:
            supabase.table("recept").update({
                "is_public": st.session_state["bok_publik_input"],
                "book_name": st.session_state["bok_namn_input"],
            }).eq("user_id", st.session_state["user"].id).execute()
            st.success("Inställningarna har sparats!")
            st.rerun()
        except Exception as e:
            st.error(f"Kunde inte uppdatera: {e}")

    st.divider()

    st.subheader("👥 Andras Receptböcker")
    try:
        publika_res = supabase.table("recept").select("*").eq("is_public", True).execute()
        publika_recept = publika_res.data or []

        if not publika_recept:
            st.info("Inga publika receptböcker hittades.")
            st.session_state["vald_delad_användare"] = None
            st.session_state["användare_dict"] = {}
            st.session_state["book_name_dict"] = {}
        else:
            användare_dict = {}
            book_name_dict = {}
            for rec in publika_recept:
                uid = rec.get("user_id")
                if not uid:
                    continue
                if uid not in användare_dict:
                    användare_dict[uid] = []
                    book_name_dict[uid] = rec.get("book_name") or f"Receptbok ({uid[:6]}...)"
                användare_dict[uid].append(rec)

            options_list = [None] + list(användare_dict.keys())
            if st.session_state.get("select_delad_bok") not in options_list:
                st.session_state["select_delad_bok"] = None

            def format_func(x):
                if x is None:
                    return "🏠 Visa Mina Egna Recept"
                return f"📖 {book_name_dict.get(x, 'Receptbok')}"

            vald_användare = st.selectbox(
                "Välj bok att läsa:", options=options_list,
                format_func=format_func, key="select_delad_bok"
            )
            st.session_state["vald_delad_användare"] = vald_användare
            st.session_state["användare_dict"] = användare_dict
            st.session_state["book_name_dict"] = book_name_dict

    except Exception:
        st.error("Kunde inte hämta delade böcker.")
        st.session_state["vald_delad_användare"] = None
        st.session_state["användare_dict"] = {}
        st.session_state["book_name_dict"] = {}

    if ADMIN_EMAIL and st.session_state["user"].email == ADMIN_EMAIL:
        st.divider()
        st.subheader("👤 Åtkomstlista")
        try:
            förfrågningar = admin_supabase.table("konto_forfragningar").select("*").order("skapad").execute().data or []
        except Exception as e:
            st.error(f"Kunde inte hämta listan: {e}")
            förfrågningar = []

        with st.form("lagg_till_adress"):
            tillagg_email = st.text_input("Lägg till adress direkt", key="tillagg_email")
            tillagg = st.form_submit_button("Lägg till")
            if tillagg:
                if not tillagg_email or "@" not in tillagg_email:
                    st.error("Ange en giltig e-postadress.")
                else:
                    try:
                        admin_supabase.table("konto_forfragningar").upsert({
                            "email": tillagg_email.strip().lower(),
                            "status": "tillaten",
                        }, on_conflict="email").execute()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Kunde inte lägga till: {e}")

        väntande = [f for f in förfrågningar if f.get("status") != "tillaten"]
        tillatna = [f for f in förfrågningar if f.get("status") == "tillaten"]

        if väntande:
            st.caption("Väntande förfrågningar")
        for f in väntande:
            st.write(f"**{f['email']}**")
            c1, c2 = st.columns(2)
            if c1.button("Godkänn", key=f"ok_{f['id']}"):
                admin_supabase.table("konto_forfragningar").update({
                    "status": "tillaten",
                }).eq("id", f["id"]).execute()
                st.rerun()
            if c2.button("Neka", key=f"nej_{f['id']}"):
                admin_supabase.table("konto_forfragningar").delete().eq("id", f["id"]).execute()
                st.rerun()

        if tillatna:
            st.caption("Godkända adresser")
        for f in tillatna:
            c1, c2 = st.columns([4, 1])
            c1.write(f"✅ {f['email']}")
            if c2.button("Ta bort", key=f"bort_{f['id']}"):
                admin_supabase.table("konto_forfragningar").delete().eq("id", f["id"]).execute()
                st.rerun()

        if not förfrågningar:
            st.caption("Listan är tom.")

    st.divider()

    if st.button("Logga ut", type="secondary"):
        try:
            supabase.auth.sign_out()
        except Exception:
            pass
        st.session_state["auth_session"] = None
        st.session_state["user"] = None
        st.rerun()

if "uploader_key" not in st.session_state:
    st.session_state["uploader_key"] = 0

aktiv_delad_användare = st.session_state.get("vald_delad_användare", None)

if aktiv_delad_användare:
    b_namn = st.session_state.get("book_name_dict", {}).get(aktiv_delad_användare, "Receptbok")
    st.info(f"📖 Du läser nu den publika receptboken: **{b_namn}**")

    sokord_delat = st.text_input("🔍 Sök i denna publika receptbok...", "", key="sok_delat")
    delade_recept_lista = st.session_state.get("användare_dict", {}).get(aktiv_delad_användare, [])

    for r in delade_recept_lista:
        titel = r.get("title") or r.get("titel") or "Namnlöst recept"
        kategori = r.get("category") or r.get("kategori") or "Övrigt"
        ingredients_d = r.get("ingredients") or r.get("text") or ""
        instructions_d = r.get("instructions") or ""

        if (not sokord_delat
                or sokord_delat.lower() in str(titel).lower()
                or sokord_delat.lower() in str(ingredients_d).lower()
                or sokord_delat.lower() in str(instructions_d).lower()):
            with st.expander(f"📌 {titel} ({kategori})"):
                rendera_receptvisning(r, f"delat_{r.get('id') or uuid.uuid4().hex}")

else:
    flik1, flik2, flik3 = st.tabs(["📷 Skanna Recept", "📚 Mina Recept", "🔍 Sök Recept"])

    with flik1:
        st.header("Skanna handskrivet recept")

        uppladdad_bild = st.file_uploader(
            "Välj bild på recept...",
            type=["jpg", "jpeg", "png"],
            key=f"uploader_{st.session_state['uploader_key']}"
        )

        if uppladdad_bild:
            bild_signatur = f"{uppladdad_bild.name}_{uppladdad_bild.size}"
            if st.session_state.get("analys_signatur") != bild_signatur:
                st.session_state.pop("analyserat_recept", None)
                st.session_state["analys_signatur"] = bild_signatur

            bild = Image.open(uppladdad_bild)
            bild = ImageOps.exif_transpose(bild)
            if bild.mode != "RGB":
                bild = bild.convert("RGB")

            vinkel = st.selectbox(
                "Rotera bild om den ligger fel:",
                options=[0, 90, 180, 270],
                format_func=lambda v: "Ingen rotation" if v == 0 else f"{v}° medurs",
                key=f"rotation_{st.session_state['uploader_key']}",
            )
            if vinkel:
                bild = bild.rotate(-vinkel, expand=True)

            bild.thumbnail((1600, 1600))
            st.image(bild, caption="Uppladdad bild (optimerad)", width=300)

            if st.button("Analysera recept med AI", key="btn_analysera"):
                with st.spinner("Läser ingredienser, steg och näring..."):
                    try:
                        meta_prompt = (
                            "Läs av receptet på bilden. Returnera ENDAST JSON med nycklarna "
                            "'title', 'category', 'portioner_antal', 'portioner_enhet' och 'ingredients'. "
                            "'ingredients' ska vara en sträng där varje ingrediens ligger på egen rad med "
                            "bindestreck först, med mått, exakt som i originalet. "
                            "Hitta portionsangivelsen om den finns (t.ex. '1 portion', '4 personer', '20 kakor') "
                            "och lägg den i 'portioner_antal' och 'portioner_enhet', inte i ingredienslistan. "
                            "Svara utan markdown-kodblock."
                        )

                        steg_prompt = (
                            "Skriv tillagningsstegen från bilden i nyckeln 'instructions'. "
                            "Sammanfatta varje steg kort med egna ord, en mening per steg, utan numrering. "
                            "Returnera ENDAST JSON med den nyckeln."
                        )

                        nar_prompt = (
                            "Uppskatta näringsvärden per portion och per 100 g för receptet på bilden. "
                            "Returnera ENDAST JSON med nyckeln 'nutrition', som ett objekt med 'per_portion' "
                            "och 'per_100g', vilka i sin tur har 'energi', 'protein', 'kolhydrater', 'socker', "
                            "'fett', 'mattat_fett', 'fiber', 'salt', 'c_vitamin', 'd_vitamin' och 'jarn'."
                        )

                        def anropa(text_prompt):
                            return client.models.generate_content(
                                model=GEMINI_MODEL,
                                contents=[bild, text_prompt],
                                config={"max_output_tokens": 4096, "thinking_config": {"thinking_budget": 0}},
                            )

                        def med_omforsok(text_prompt):
                            """Två försök: rakt av, sedan med skarpare krav på egna ord."""
                            orsak = "okänt"
                            for i in range(2):
                                p = text_prompt
                                if i == 1:
                                    p += " Formulera dig med egna ord, inte ordagrant efter originalet."
                                r = anropa(p)
                                try:
                                    svar = r.text if r is not None else None
                                except Exception:
                                    svar = None
                                if svar:
                                    return svar, ""
                                try:
                                    orsak = str(r.candidates[0].finish_reason)
                                except Exception:
                                    pass
                            return None, orsak

                        delar = {}
                        orsaker = []
                        for nyckel, text_prompt in (
                            ("grund", meta_prompt),
                            ("steg", steg_prompt),
                            ("naring", nar_prompt),
                        ):
                            svar, orsak = med_omforsok(text_prompt)
                            if svar:
                                delar[nyckel] = svar
                            else:
                                orsaker.append(f"{nyckel}: {orsak}")

                        rec = {}
                        for nyckel in ("grund", "steg", "naring"):
                            if nyckel in delar:
                                try:
                                    rec.update(extrahera_json(delar[nyckel]))
                                except Exception:
                                    pass

                        if not rec.get("ingredients"):
                            st.error("Kunde inte läsa receptet.")
                            if orsaker:
                                st.caption(" | ".join(orsaker))
                        else:
                            st.session_state["analyserat_recept"] = rec
                            if orsaker:
                                st.warning("Vissa delar kunde inte läsas: " + " | ".join(orsaker))
                            st.success("Receptet har analyserats!")

                    except Exception as e:
                        st.error(f"Ett fel uppstod vid anropet till Gemini (modell: {GEMINI_MODEL}): {e}")

            if "analyserat_recept" in st.session_state:
                rec = st.session_state["analyserat_recept"]
                naring_data = rec.get("nutrition") or {}
                if not isinstance(naring_data, dict):
                    naring_data = {}

                st.subheader("Granska & Spara")

                col_titel, col_kat, col_port, col_enh = st.columns([2, 1, 1, 1])
                with col_titel:
                    titel_input = st.text_input("Titel", value=rec.get("title", ""))
                with col_kat:
                    kategori_input = st.text_input("Kategori", value=rec.get("category", "Övrigt"))
                with col_port:
                    portioner_input = st.number_input(
                        "Antal", min_value=1, max_value=500,
                        value=till_int(rec.get("portioner_antal"), 4)
                    )
                with col_enh:
                    enhet_input = st.text_input("Enhet", value=rec.get("portioner_enhet", "portioner"))

                ingredients_input = st.text_area(
                    "Ingredienser (en på varje rad)",
                    value=formatera_ingredienser(rec.get("ingredients", "")), height=150
                )
                instructions_input = st.text_area(
                    "Tillagningssteg (utan numrering)",
                    value=ta_bort_numrering(rec.get("instructions", "")), height=150
                )

                st.markdown("### 📊 Näringsinnehåll & Vitaminer")
                df_naring = bygg_naringstabell(naring_data)
                if df_naring is not None:
                    st.table(df_naring)
                else:
                    st.caption("Inga näringsvärden kunde uppskattas.")

                if st.button("💾 Spara till databasen", key="btn_spara"):
                    with st.spinner("Laddar upp bild och sparar recept..."):
                        try:
                            existing_meta = supabase.table("recept").select("book_name, is_public").eq("user_id", st.session_state["user"].id).limit(1).execute()
                            current_book_name = "Anonymkokboken"
                            current_is_public = False
                            if existing_meta.data:
                                current_book_name = existing_meta.data[0].get("book_name") or "Anonymkokboken"
                                current_is_public = bool(existing_meta.data[0].get("is_public", False))

                            image_url = None
                            if uppladdad_bild is not None:
                                file_ext = uppladdad_bild.name.rsplit(".", 1)[-1].lower()
                                file_name = f"{st.session_state['user'].id}/{uuid.uuid4()}.{file_ext}"

                                uppladdad_bild.seek(0)
                                supabase.storage.from_(BUCKET).upload(
                                    path=file_name,
                                    file=uppladdad_bild.getvalue(),
                                    file_options={"content-type": CONTENT_TYPES.get(file_ext, "application/octet-stream")}
                                )

                                url_res = supabase.storage.from_(BUCKET).get_public_url(file_name)
                                image_url = url_res.get("publicURL") if isinstance(url_res, dict) else url_res

                            supabase.table("recept").insert({
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
                                "book_name": current_book_name,
                            }).execute()

                            st.success("Recept och bild har sparats!")
                            st.session_state.pop("analyserat_recept", None)
                            st.session_state.pop("analys_signatur", None)
                            st.session_state["uploader_key"] += 1
                            st.rerun()
                        except Exception as e:
                            st.error(f"Kunde inte spara till databasen: {e}")

    with flik2:
        st.header("Mina sparade recept")
        sokord = st.text_input("🔍 Sök i dina recept...", "", key="sok_recept")

        try:
            res = supabase.table("recept").select("*").eq("user_id", st.session_state["user"].id).execute()
            recept_lista = res.data or []

            if not recept_lista:
                st.info("Inga sparade recept hittades.")
            else:
                for r in recept_lista:
                    titel = r.get("title") or r.get("titel") or "Namnlöst recept"
                    kategori = r.get("category") or r.get("kategori") or "Övrigt"
                    ingredients = r.get("ingredients") or ""
                    instructions = r.get("instructions") or ""
                    orig_portioner = till_int(r.get("portioner") or r.get("servings"), 4)
                    orig_enhet = r.get("portioner_enhet") or "portioner"
                    recept_id = r.get("id")
                    nutrition_dict = r.get("nutrition_json") or {}

                    if not ingredients and not instructions and r.get("text"):
                        ingredients = r.get("text")

                    if (not sokord
                            or sokord.lower() in str(titel).lower()
                            or sokord.lower() in str(ingredients).lower()
                            or sokord.lower() in str(instructions).lower()):
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
                                    ny_portioner = st.number_input(
                                        "Antal (original)", min_value=1, max_value=500,
                                        value=orig_portioner, key=f"ny_port_{recept_id}"
                                    )
                                with col_e2:
                                    ny_enhet = st.text_input("Enhet", value=orig_enhet, key=f"ny_enh_{recept_id}")

                                ny_ingredients = st.text_area(
                                    "Ingredienser (en på varje rad)",
                                    value=formatera_ingredienser(ingredients),
                                    height=150, key=f"ny_ing_{recept_id}"
                                )
                                ny_instructions = st.text_area(
                                    "Tillagningssteg (utan numrering)",
                                    value=ta_bort_numrering(instructions),
                                    height=150, key=f"ny_inst_{recept_id}"
                                )

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
                                                "instructions": ny_instructions,
                                            }).eq("id", recept_id).eq("user_id", st.session_state["user"].id).execute()
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

                                valda_portioner = st.session_state.get(f"portioner_egen_{recept_id}", orig_portioner)
                                skalad_ing = skala_ingredienser(ingredients, orig_portioner, valda_portioner)

                                naring_text_lines = []
                                p_data_c, h_data_c = _naring_ramar(nutrition_dict)
                                for lbl, ky in NARINGS_ETIKETTER:
                                    hv = h_data_c.get(ky, "-")
                                    pv = p_data_c.get(ky, "-")
                                    if hv != "-" or pv != "-":
                                        naring_text_lines.append(f" - {lbl}: {hv} per 100g, {pv} per portion")

                                naring_copy_block = (
                                    "\n\nNäringsinnehåll & Vitaminer:\n" + "\n".join(naring_text_lines)
                                    if naring_text_lines else ""
                                )
                                rensad_instruktion = ta_bort_numrering(instructions)
                                recept_innehall = (
                                    f"Recept: {titel}\nKategori: {kategori}\n\n"
                                    f"Ingredienser ({valda_portioner} {orig_enhet}):\n{formatera_ingredienser(skalad_ing)}\n\n"
                                    f"Tillagning:\n{rensad_instruktion}{naring_copy_block}"
                                )
                                safe_text = json.dumps(recept_innehall)

                                print_naring_html = ""
                                p_data_p, h_data_p = _naring_ramar(nutrition_dict)
                                if p_data_p or h_data_p:
                                    rader_html = "".join(
                                        f'<tr style="border-bottom: 1px solid #eee;">'
                                        f'<td style="padding: 5px;">{html.escape(lbl)}</td>'
                                        f'<td style="padding: 5px;">{html.escape(str(h_data_p.get(ky, "-")))}</td>'
                                        f'<td style="padding: 5px;">{html.escape(str(p_data_p.get(ky, "-")))}</td>'
                                        f'</tr>'
                                        for lbl, ky in NARINGS_ETIKETTER
                                    )
                                    print_naring_html = (
                                        '<h3>📊 Näringsinnehåll & Vitaminer</h3><table>'
                                        '<thead><tr style="background-color: #f2f2f2; border-bottom: 1px solid #ddd;">'
                                        '<th style="text-align: left; padding: 6px;">Näringsämne</th>'
                                        '<th style="text-align: left; padding: 6px;">Per 100 g</th>'
                                        '<th style="text-align: left; padding: 6px;">Per Portion</th>'
                                        f'</tr></thead><tbody>{rader_html}</tbody></table>'
                                    )

                                utskriftsdokument = bygg_utskriftsdokument(
                                    titel, kategori, f"{valda_portioner} {orig_enhet}",
                                    formatera_ingredienser(skalad_ing), rensad_instruktion,
                                    print_naring_html
                                )
                                safe_dokument = json.dumps(utskriftsdokument)

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
                                        win.document.write({safe_dokument});
                                        win.document.close();
                                        win.focus();
                                        setTimeout(function () {{
                                            win.print();
                                            win.close();
                                        }}, 500);
                                    }}

                                    function copyRecipe() {{
                                        const textToShare = {safe_text};
                                        const statusEl = document.getElementById('status');
                                        navigator.clipboard.writeText(textToShare).then(function () {{
                                            statusEl.innerText = 'Kopierat!';
                                            setTimeout(function () {{ statusEl.innerText = ''; }}, 2000);
                                        }}).catch(function () {{
                                            statusEl.innerText = 'Kunde inte kopiera';
                                        }});
                                    }}
                                    </script>
                                </body>
                                </html>
                                """
                                components.html(print_html, height=45)

                                if st.button("🗑️ Radera recept", key=f"del_{recept_id}"):
                                    try:
                                        supabase.table("recept").delete().eq("id", recept_id).eq("user_id", st.session_state["user"].id).execute()
                                        st.success("Receptet har raderats!")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Kunde inte radera: {e}")

        except Exception as e:
            st.error(f"Ett fel uppstod vid hämtning av dina recept: {e}")

    with flik3:
        st.header("🔍 Sök recept")
        st.caption("Sök bland dina sparade recept och få nya AI-förslag baserat på ingredienser.")

        st.markdown(
            """
            <style>
            div[data-testid="stExpander"] { margin: 0 0 2px 0 !important; padding: 0 !important; }
            div[data-testid="stExpander"] > details { margin: 0 !important; padding: 0 !important; }
            div[data-testid="stExpander"] > details > summary { padding: 1px 6px !important; min-height: 0 !important; }
            div[data-testid="stExpander"] > details > summary p { font-size: 0.9rem !important; margin: 0 !important; }
            div[data-testid="stVerticalBlock"] { gap: 0.25rem !important; }
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
                st.session_state["sok_state"] = {
                    "input": ingrediens_input,
                    "ai_forslag": [],
                    "har_hamtat": False,
                    "id": uuid.uuid4().hex[:8],
                }

        if ingrediens_input and st.session_state["sok_state"] is not None:
            state = st.session_state["sok_state"]
            sokte_ingredienser = [i.strip().lower() for i in ingrediens_input.split(",") if i.strip()]

            egna_traffar = []
            try:
                q = supabase.table("recept").select("*").eq("user_id", st.session_state["user"].id)
                or_delar = []
                for ing in sokte_ingredienser:
                    rent = ing.replace(",", " ").replace("%", "").replace("*", "").strip()
                    if not rent:
                        continue
                    or_delar.append(f"title.ilike.*{rent}*")
                    or_delar.append(f"ingredients.ilike.*{rent}*")
                if or_delar:
                    q = q.or_(",".join(or_delar))
                res = q.execute()
                for r in (res.data or []):
                    sok_text = (str(r.get("title", "")) + " " + str(r.get("ingredients", ""))).lower()
                    matches = sum(1 for ing in sokte_ingredienser if ing in sok_text)
                    if matches > 0:
                        egna_traffar.append((matches, r))
                egna_traffar.sort(key=lambda x: x[0], reverse=True)
            except Exception as e:
                st.error(f"Kunde inte hämta recept: {e}")

            if egna_traffar:
                st.subheader("Dina sparade recept")
                for score, rec_data in egna_traffar:
                    r_id = rec_data.get("id") or uuid.uuid4().hex
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
                    "portioner": till_int(rd.get("portioner") or rd.get("portioner_antal"), 4),
                    "portioner_enhet": rd.get("portioner_enhet") or "portioner",
                    "ingredients": rd.get("ingredients"),
                    "instructions": rd.get("instructions"),
                    "nutrition_json": rd.get("nutrition") or {},
                }

            def spara_ai(norm):
                try:
                    existing_meta = supabase.table("recept").select("book_name, is_public").eq("user_id", st.session_state["user"].id).limit(1).execute()
                    current_book_name = "Anonymkokboken"
                    current_is_public = False
                    if existing_meta.data:
                        current_book_name = existing_meta.data[0].get("book_name") or "Anonymkokboken"
                        current_is_public = bool(existing_meta.data[0].get("is_public", False))

                    supabase.table("recept").insert({
                        "user_id": st.session_state["user"].id,
                        "title": norm["title"],
                        "category": norm["category"],
                        "portioner": norm["portioner"],
                        "portioner_enhet": norm["portioner_enhet"],
                        "ingredients": norm["ingredients"],
                        "instructions": norm["instructions"],
                        "nutrition_json": norm["nutrition_json"],
                        "image_url": None,
                        "is_public": current_is_public,
                        "book_name": current_book_name,
                    }).execute()
                    st.toast("Receptet har sparats!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Kunde inte spara receptet: {e}")

            def rita_ai_kort(rd, idx):
                norm = normalisera(rd)
                with ai_plats.expander(f"🤖 {norm['title'] or 'Förslag'} — {norm['category'] or 'Middag'}", expanded=False):
                    rendera_receptvisning(norm, f"sok_ai_{state['id']}_{idx}", kompakt=True)
                    if st.button("💾 Spara till Mina recept", key=f"spara_ai_{state['id']}_{idx}"):
                        spara_ai(norm)

            def ett_anrop(inriktning, befintliga_titlar):
                prompt = (
                    f"Generera 3 goda recept av typen {inriktning} som matchar följande ingredienser: {ingrediens_input}. "
                    f"Undvik dessa redan föreslagna titlar: {', '.join(befintliga_titlar) if befintliga_titlar else 'inga'}. "
                    "Returnera ett recept i taget som enskilda JSON-objekt, ETT per rad, utan array-klamrar. "
                    "Varje objekt ska ha nycklarna:\n"
                    "- 'title': Receptets namn\n"
                    "- 'category': Kategori\n"
                    "- 'portioner_antal': Antal portioner som heltal\n"
                    "- 'portioner_enhet': Enhet (t.ex. portioner)\n"
                    "- 'ingredients': Ingredienser på egen rad, med bindestreck i början\n"
                    "- 'instructions': Tillagningssteg utan numrering\n"
                    "- 'nutrition': Objekt med 'per_portion' och 'per_100g': 'energi', 'protein', 'kolhydrater', 'socker', 'fett', 'mattat_fett', 'fiber', 'salt', 'c_vitamin', 'd_vitamin', 'jarn'"
                )
                buffert = {"rad": ""}
                resultat = []

                def ta_rad(rad):
                    rad = rad.strip().rstrip(",")
                    if not rad.startswith("{") or not rad.endswith("}"):
                        return
                    try:
                        resultat.append(json.loads(rad))
                    except json.JSONDecodeError:
                        pass

                for chunk in client.models.generate_content_stream(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config={"max_output_tokens": 4096, "thinking_config": {"thinking_budget": 0}}
                ):
                    try:
                        bit = chunk.text
                    except Exception:
                        bit = None
                    if bit:
                        buffert["rad"] += bit
                        while "\n" in buffert["rad"]:
                            klar, buffert["rad"] = buffert["rad"].split("\n", 1)
                            ta_rad(klar)

                if buffert["rad"].strip():
                    ta_rad(buffert["rad"])
                return resultat

            def kor_parallellt(tidsgrans=60):
                befintliga = [f.get("title", "") for f in state["ai_forslag"]]
                inriktningar = ["huvudrätt", "förrätt eller tillbehör", "efterrätt eller bakverk"]
                insamlade = []
                fel = []
                ex = ThreadPoolExecutor(max_workers=3)
                futurer = {ex.submit(ett_anrop, inr, befintliga): inr for inr in inriktningar}
                try:
                    for f in as_completed(futurer, timeout=tidsgrans):
                        try:
                            insamlade.extend(f.result())
                        except Exception as e:
                            fel.append(f"{futurer[f]}: {e}")
                except TimeoutError:
                    fel.append(f"Tidsgränsen på {tidsgrans} sekunder nåddes")
                except Exception as e:
                    fel.append(str(e))
                finally:
                    ex.shutdown(wait=False)

                titlar = {str(f.get("title", "")).lower() for f in state["ai_forslag"]}
                for rd in insamlade:
                    t = str(rd.get("title", "")).lower()
                    if t and t not in titlar:
                        titlar.add(t)
                        state["ai_forslag"].append(rd)

                if insamlade:
                    state["har_hamtat"] = True
                return fel

            if not state["har_hamtat"]:
                st.caption("Kan ta upp till en minut.")
                with st.spinner("Tar fram receptförslag..."):
                    fel = kor_parallellt()
                if state["ai_forslag"]:
                    st.rerun()
                else:
                    for f in fel:
                        st.warning(f)
                    if st.button("Försök igen", key="ai_igen"):
                        st.rerun()
            else:
                for i, rd in enumerate(state["ai_forslag"]):
                    rita_ai_kort(rd, i)

            if state["ai_forslag"]:
                if st.button("➕ Visa fler recept"):
                    with st.spinner("Hämtar fler förslag..."):
                        kor_parallellt()
                        st.rerun()
            elif state["har_hamtat"]:
                st.info("Inga AI-förslag hittades för dessa ingredienser.")
