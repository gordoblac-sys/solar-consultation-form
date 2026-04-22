import os
import re
import ssl
import csv
import base64
import mimetypes
import smtplib
from io import BytesIO
from datetime import datetime, timezone
from email.message import EmailMessage

from flask import Flask, request, render_template_string, send_file, jsonify, url_for
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
import qrcode
from werkzeug.utils import secure_filename

app = Flask(__name__)

# -------------------------------------------------
# BASE PATHS
# -------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_FOLDER = os.path.join(BASE_DIR, "static")
DATA_FOLDER = os.path.join(BASE_DIR, "data")
TEMP_FOLDER = os.path.join(BASE_DIR, "temp_files")

os.makedirs(STATIC_FOLDER, exist_ok=True)
os.makedirs(DATA_FOLDER, exist_ok=True)
os.makedirs(TEMP_FOLDER, exist_ok=True)

# -------------------------------------------------
# SETTINGS
# -------------------------------------------------
SEND_EMAIL = True
DELETE_LOCAL_FILES_AFTER_EMAIL = True

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USERNAME)
EMAIL_TO = os.environ.get("EMAIL_TO", "solarleadership@safestreets.com")

TERMS_URL = "https://www.safestreets.com/terms-conditions/"
PRIVACY_URL = "https://www.safestreets.com/privacy-policy/"
DO_NOT_SELL_URL = "https://www.safestreets.com/affirmation/"

CONSENT_VERSION = "solar-consultation-agreement-v12"

# Exact filenames in static/
# If your actual filename spelling is different, update it here.
LOGO_FILENAME = "SafetreetsLogo.png"
FIVE_STAR_FILENAME = "Safestreets5Star.png"
SHIELD_FILENAME = "shield.png"
BACKGROUND_FILENAME = "background.jpg"

# -------------------------------------------------
# STATIC HELPERS
# -------------------------------------------------
def logo_exists():
    return os.path.exists(os.path.join(STATIC_FOLDER, LOGO_FILENAME))

def five_star_exists():
    return os.path.exists(os.path.join(STATIC_FOLDER, FIVE_STAR_FILENAME))

def shield_exists():
    return os.path.exists(os.path.join(STATIC_FOLDER, SHIELD_FILENAME))

def background_exists():
    return os.path.exists(os.path.join(STATIC_FOLDER, BACKGROUND_FILENAME))

def common_template_context():
    return {
        "logo_exists": logo_exists(),
        "five_star_exists": five_star_exists(),
        "shield_exists": shield_exists(),
        "background_exists": background_exists(),
    }

# -------------------------------------------------
# STATES
# -------------------------------------------------
STATES = [
    ("", "Select State"),
    ("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"), ("AR", "Arkansas"),
    ("CA", "California"), ("CO", "Colorado"), ("CT", "Connecticut"), ("DE", "Delaware"),
    ("FL", "Florida"), ("GA", "Georgia"), ("HI", "Hawaii"), ("ID", "Idaho"),
    ("IL", "Illinois"), ("IN", "Indiana"), ("IA", "Iowa"), ("KS", "Kansas"),
    ("KY", "Kentucky"), ("LA", "Louisiana"), ("ME", "Maine"), ("MD", "Maryland"),
    ("MA", "Massachusetts"), ("MI", "Michigan"), ("MN", "Minnesota"), ("MS", "Mississippi"),
    ("MO", "Missouri"), ("MT", "Montana"), ("NE", "Nebraska"), ("NV", "Nevada"),
    ("NH", "New Hampshire"), ("NJ", "New Jersey"), ("NM", "New Mexico"), ("NY", "New York"),
    ("NC", "North Carolina"), ("ND", "North Dakota"), ("OH", "Ohio"), ("OK", "Oklahoma"),
    ("OR", "Oregon"), ("PA", "Pennsylvania"), ("RI", "Rhode Island"), ("SC", "South Carolina"),
    ("SD", "South Dakota"), ("TN", "Tennessee"), ("TX", "Texas"), ("UT", "Utah"),
    ("VT", "Vermont"), ("VA", "Virginia"), ("WA", "Washington"), ("WV", "West Virginia"),
    ("WI", "Wisconsin"), ("WY", "Wyoming")
]

def render_state_options(selected_state=""):
    html = []
    for code, name in STATES:
        selected = "selected" if code == selected_state else ""
        html.append(f'<option value="{code}" {selected}>{name}</option>')
    return "".join(html)

# -------------------------------------------------
# CONSENT TEXT
# -------------------------------------------------
COMBINED_CONSENT_TEXT = (
    "By signing below and submitting this form, I agree by electronic signature to receive recurring "
    "automated marketing and other calls, texts, and prerecorded messages from SafeStreets' solar partners "
    "at the number I provide, even if I am on a Do Not Call list. I authorize SafeStreets' solar partners, "
    "their partners, and/or affiliates to contact me by telephone calls and/or text messages (SMS), using "
    "auto-dialing technology or otherwise, for advertising and marketing purposes. Consent is not required "
    "to make a purchase. Message and data rates may apply. Reply STOP to opt out of texts or HELP for help. "
    "By signing and submitting, I also acknowledge and agree to the Terms of Use, Privacy Policy, and "
    "Do Not Sell My Personal Information notice linked below."
)

# -------------------------------------------------
# UTILITY DATA
# -------------------------------------------------
UTILITY_ZIP_INDEX = {}
UTILITY_STATE_INDEX = {}

def normalize_zip(zip_code: str) -> str:
    return "".join(ch for ch in (zip_code or "") if ch.isdigit())[:5]

def pick_value(row, candidates):
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for candidate in candidates:
        if candidate in lowered:
            value = lowered[candidate]
            if value is not None:
                return str(value).strip()
    return ""

def load_utility_data():
    global UTILITY_ZIP_INDEX, UTILITY_STATE_INDEX

    UTILITY_ZIP_INDEX = {}
    UTILITY_STATE_INDEX = {}

    csv_files = []
    if os.path.exists(DATA_FOLDER):
        for name in os.listdir(DATA_FOLDER):
            if name.lower().endswith(".csv"):
                csv_files.append(os.path.join(DATA_FOLDER, name))

    print("---- Utility CSV Load Start ----")
    print(f"CSV files found: {len(csv_files)}")

    total_rows = 0
    total_zip_entries_added = 0

    for file_path in csv_files:
        print(f"Loading file: {os.path.basename(file_path)}")
        file_rows = 0
        file_zip_entries = 0

        try:
            with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)

                for row in reader:
                    file_rows += 1
                    total_rows += 1

                    zip_code = normalize_zip(pick_value(row, [
                        "zip", "zipcode", "zip_code", "zip code", "postal_code", "postal code"
                    ]))

                    state = pick_value(row, [
                        "state", "state_abbr", "state abbreviation", "service_state", "state code"
                    ]).upper()

                    utility_name = pick_value(row, [
                        "utility_name", "utility", "utility company", "company_name", "company", "provider"
                    ])

                    if utility_name:
                        added_here = False

                        if zip_code:
                            if zip_code not in UTILITY_ZIP_INDEX:
                                UTILITY_ZIP_INDEX[zip_code] = set()
                            before = len(UTILITY_ZIP_INDEX[zip_code])
                            UTILITY_ZIP_INDEX[zip_code].add(utility_name)
                            after = len(UTILITY_ZIP_INDEX[zip_code])
                            if after > before:
                                added_here = True

                        if state:
                            if state not in UTILITY_STATE_INDEX:
                                UTILITY_STATE_INDEX[state] = set()
                            UTILITY_STATE_INDEX[state].add(utility_name)

                        if added_here:
                            file_zip_entries += 1
                            total_zip_entries_added += 1

            print(f"Rows read: {file_rows}")
            print(f"ZIP utility entries added: {file_zip_entries}")

        except Exception as e:
            print(f"Could not load utility CSV {file_path}: {e}")

    UTILITY_ZIP_INDEX = {k: sorted(v) for k, v in UTILITY_ZIP_INDEX.items()}
    UTILITY_STATE_INDEX = {k: sorted(v) for k, v in UTILITY_STATE_INDEX.items()}

    print(f"Total rows read: {total_rows}")
    print(f"ZIP codes loaded: {len(UTILITY_ZIP_INDEX)}")
    print(f"States loaded: {len(UTILITY_STATE_INDEX)}")
    print(f"Total ZIP utility entries added: {total_zip_entries_added}")
    print("---- Utility CSV Load End ----")

def get_utility_options(zip_code: str, state: str):
    zip_code = normalize_zip(zip_code)
    state = (state or "").upper().strip()

    if zip_code in UTILITY_ZIP_INDEX:
        return UTILITY_ZIP_INDEX[zip_code]

    if state in UTILITY_STATE_INDEX:
        return UTILITY_STATE_INDEX[state]

    return []

load_utility_data()

# -------------------------------------------------
# STYLES
# -------------------------------------------------
BASE_STYLES = """
<style>
    :root {
        --ss-blue: #0b2f5b;
        --ss-blue-2: #184d8a;
        --ss-blue-3: #2b6cb0;
        --ss-orange: #f59e0b;
        --ss-orange-2: #d97706;
        --ss-white: #ffffff;
        --ss-border: #d9e4f2;
        --ss-text: #152235;
        --ss-muted: #56657a;
        --ss-danger: #b42318;
        --ss-shadow: 0 12px 35px rgba(11, 47, 91, 0.22);
    }

    * { box-sizing: border-box; }

    body {
        margin: 0;
        font-family: Arial, Helvetica, sans-serif;
        color: var(--ss-text);
        {% if background_exists %}
        background-image:
            linear-gradient(rgba(8, 19, 35, 0.55), rgba(8, 19, 35, 0.55)),
            url('{{ url_for("static", filename="background.jpg") }}');
        background-size: cover;
        background-position: center center;
        background-repeat: no-repeat;
        background-attachment: fixed;
        {% else %}
        background:
            linear-gradient(180deg, #eef4fb 0%, #f8fbff 100%);
        {% endif %}
    }

    .page {
        min-height: 100vh;
        padding: 20px 14px 40px;
    }

    .container {
        max-width: 1000px;
        margin: 0 auto;
    }

    .hero {
        background: linear-gradient(135deg, rgba(11,47,91,0.92) 0%, rgba(24,77,138,0.90) 65%, rgba(43,108,176,0.88) 100%);
        color: white;
        border-radius: 24px;
        box-shadow: var(--ss-shadow);
        padding: 26px 24px;
        text-align: center;
        overflow: hidden;
        position: relative;
        backdrop-filter: blur(4px);
    }

    .hero::after {
        content: "";
        position: absolute;
        top: -60px;
        right: -60px;
        width: 200px;
        height: 200px;
        background: rgba(255,255,255,0.08);
        border-radius: 50%;
    }

    .logo-wrap {
        position: relative;
        z-index: 2;
    }

    .brand-logo {
        display: block;
        max-width: min(100%, 520px);
        width: 100%;
        height: auto;
        margin: 0 auto 14px;
    }

    .brand-shield {
        display: block;
        width: 82px;
        height: auto;
        margin: 0 auto 14px;
    }

    .brand-fallback {
        font-size: 26px;
        font-weight: 700;
        margin-bottom: 12px;
    }

    .hero h1 {
        margin: 0 0 10px;
        font-size: clamp(28px, 4vw, 42px);
        line-height: 1.15;
    }

    .hero p {
        margin: 0;
        font-size: clamp(15px, 2vw, 18px);
        line-height: 1.6;
        color: rgba(255,255,255,0.92);
    }

    .card {
        margin-top: 20px;
        background: rgba(255,255,255,0.94);
        border: 1px solid rgba(217, 228, 242, 0.9);
        border-radius: 22px;
        box-shadow: var(--ss-shadow);
        padding: 24px;
        backdrop-filter: blur(7px);
    }

    .section-title {
        margin: 0 0 8px;
        color: var(--ss-blue);
        font-size: clamp(24px, 3vw, 32px);
    }

    .section-subtitle {
        margin: 0 0 18px;
        color: var(--ss-muted);
        line-height: 1.6;
        font-size: 16px;
    }

    .error {
        background: #fff1f1;
        border: 1px solid #f3c0c0;
        color: var(--ss-danger);
        padding: 12px 14px;
        border-radius: 12px;
        font-weight: 700;
        margin-bottom: 14px;
    }

    .consent-box {
        margin-top: 20px;
        padding: 18px;
        border-radius: 16px;
        background: rgba(249, 251, 254, 0.95);
        border: 1px solid var(--ss-border);
        line-height: 1.7;
    }

    .links {
        margin-top: 12px;
        font-size: 14px;
        line-height: 1.6;
    }

    .links a {
        color: var(--ss-blue);
        font-weight: 700;
        text-decoration: none;
    }

    .links a:hover {
        text-decoration: underline;
    }

    button {
        border: none;
        border-radius: 14px;
        padding: 16px 18px;
        width: 100%;
        cursor: pointer;
        font-size: 17px;
        font-weight: 700;
        transition: 0.18s ease;
    }

    button:hover {
        transform: translateY(-1px);
        opacity: 0.98;
    }

    .submit-btn {
        margin-top: 22px;
        background: linear-gradient(135deg, var(--ss-orange) 0%, var(--ss-orange-2) 100%);
        color: white;
    }

    .clear-btn {
        width: auto;
        min-width: 170px;
        background: #eef3f9;
        color: var(--ss-blue);
        border: 1px solid var(--ss-border);
    }

    .grid-2 {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 16px;
    }

    .grid-3 {
        display: grid;
        grid-template-columns: 2fr 1fr 1fr;
        gap: 16px;
    }

    label {
        display: block;
        margin-top: 14px;
        font-weight: 700;
        color: var(--ss-blue);
    }

    input, select {
        width: 100%;
        margin-top: 8px;
        padding: 13px 14px;
        border-radius: 12px;
        border: 1px solid var(--ss-border);
        background: white;
        font-size: 16px;
        color: var(--ss-text);
    }

    input[type="file"] {
        padding: 10px;
    }

    .small {
        color: var(--ss-muted);
        font-size: 14px;
        line-height: 1.6;
        margin-top: 6px;
    }

    .sig-wrap {
        margin-top: 18px;
    }

    #signature-pad {
        width: 100%;
        height: 230px;
        border: 2px dashed #bdd0e6;
        background: #fff;
        border-radius: 16px;
        touch-action: none;
        display: block;
    }

    @media (max-width: 900px) {
        .grid-3 {
            grid-template-columns: 1fr;
        }
    }

    @media (max-width: 768px) {
        body {
            background-attachment: scroll;
        }

        .page {
            padding: 12px 10px 28px;
        }

        .hero, .card {
            border-radius: 18px;
            padding: 18px;
        }

        .grid-2,
        .grid-3 {
            grid-template-columns: 1fr;
        }

        #signature-pad {
            height: 210px;
        }
    }
</style>
"""

# -------------------------------------------------
# TEMPLATES
# -------------------------------------------------
HERO_BRAND = """
{% if logo_exists %}
    <img src="{{ url_for('static', filename='SafetreetsLogo.png') }}" alt="SafeStreets Logo" class="brand-logo">
{% elif shield_exists %}
    <img src="{{ url_for('static', filename='shield.png') }}" alt="SafeStreets Shield" class="brand-shield">
{% else %}
    <div class="brand-fallback">SafeStreets LLC</div>
{% endif %}
"""

FORM_HTML = """
<!doctype html>
<html>
<head>
    <title>SafeStreets Solar Consultation Request</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
""" + BASE_STYLES + """
</head>
<body>
    <div class="page">
        <div class="container">
            <div class="hero">
                <div class="logo-wrap">
""" + HERO_BRAND + """
                    <h1>Solar Consultation Request</h1>
                    <p>Please provide your information below and sign electronically to continue.</p>
                </div>
            </div>

            <div class="card">
                <h2 class="section-title">Customer Information</h2>
                <p class="section-subtitle">
                    This form automatically adjusts for mobile devices and laptops.
                </p>

                {% if error %}
                    <div class="error">{{ error }}</div>
                {% endif %}

                <form method="POST" action="/submit" enctype="multipart/form-data" onsubmit="return prepareSignature();">

                    <div class="grid-2">
                        <div>
                            <label>Your Name</label>
                            <input type="text" name="customer_name" value="{{ prefill.customer_name }}" required>
                        </div>
                        <div>
                            <label>Phone Number</label>
                            <input type="tel" name="phone_number" value="{{ prefill.phone_number }}" required>
                        </div>
                    </div>

                    <div class="grid-2">
                        <div>
                            <label>Email</label>
                            <input type="email" name="email" value="{{ prefill.email }}" required>
                        </div>
                        <div>
                            <label>Street Address</label>
                            <input type="text" name="street_address" value="{{ prefill.street_address }}" required>
                        </div>
                    </div>

                    <div class="grid-3">
                        <div>
                            <label>State</label>
                            <select name="state" id="state" required>
                                {{ state_options_html | safe }}
                            </select>
                        </div>
                        <div>
                            <label>ZIP Code</label>
                            <input type="text" name="zip_code" id="zip_code" value="{{ prefill.zip_code }}" maxlength="10" required>
                        </div>
                        <div>
                            <label>Date Requested for the Solar Consultation</label>
                            <input type="date" name="consultation_date" value="{{ prefill.consultation_date }}" required>
                        </div>
                    </div>

                    <div class="grid-2">
                        <div>
                            <label>Utility Company Name</label>
                            <select name="utility_company" id="utility_company">
                                {{ utility_options_html | safe }}
                            </select>
                            <p class="small">Select a state and enter a 5-digit ZIP code. The utility list will update automatically. If your utility is not shown, type it manually below.</p>
                        </div>
                        <div>
                            <label>Time Requested for the Solar Consultation</label>
                            <input type="time" name="consultation_time" value="{{ prefill.consultation_time }}" required>
                        </div>
                    </div>

                    <div>
                        <label>If your utility is not listed, type it here</label>
                        <input type="text" name="utility_company_manual" id="utility_company_manual" value="{{ prefill.utility_company_manual }}">
                    </div>

                    <label>Electricity Bill (Optional)</label>
                    <input type="file" name="electric_bill" accept=".pdf,.png,.jpg,.jpeg,.webp">
                    <p class="small">Optional. Accepted file types: PDF, PNG, JPG, JPEG, WEBP</p>

                    <div class="consent-box">
                        <strong>Electronic Consent</strong>
                        <div style="margin-top:8px;">{{ combined_consent_text }}</div>
                        <div class="links">
                            <a href="{{ terms_url }}" target="_blank">Terms of Use</a> |
                            <a href="{{ privacy_url }}" target="_blank">Privacy Policy</a> |
                            <a href="{{ do_not_sell_url }}" target="_blank">Do Not Sell My Personal Information</a>
                        </div>
                    </div>

                    <div class="sig-wrap">
                        <label>Electronic Signature</label>
                        <p class="small">By signing below, you are agreeing to the disclosure above.</p>
                        <canvas id="signature-pad"></canvas>
                        <input type="hidden" name="signature_data" id="signature_data">
                        <div style="margin-top: 12px;">
                            <button type="button" class="clear-btn" onclick="clearSignature()">Clear Signature</button>
                        </div>
                    </div>

                    <button type="submit" class="submit-btn">Submit Solar Consultation Agreement</button>
                </form>
            </div>
        </div>
    </div>

    <script>
        const canvas = document.getElementById('signature-pad');
        const ctx = canvas.getContext('2d');
        let drawing = false;
        let hasSignature = false;

        function setupCanvas() {
            const rect = canvas.getBoundingClientRect();
            const ratio = Math.max(window.devicePixelRatio || 1, 1);
            canvas.width = rect.width * ratio;
            canvas.height = rect.height * ratio;
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.scale(ratio, ratio);
            ctx.lineWidth = 2;
            ctx.lineCap = 'round';
            ctx.strokeStyle = '#0b2f5b';
        }

        function point(e) {
            const rect = canvas.getBoundingClientRect();
            if (e.touches && e.touches.length > 0) {
                return {
                    x: e.touches[0].clientX - rect.left,
                    y: e.touches[0].clientY - rect.top
                };
            }
            return {
                x: e.clientX - rect.left,
                y: e.clientY - rect.top
            };
        }

        function startDraw(e) {
            e.preventDefault();
            drawing = true;
            hasSignature = true;
            const p = point(e);
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);
        }

        function draw(e) {
            if (!drawing) return;
            e.preventDefault();
            const p = point(e);
            ctx.lineTo(p.x, p.y);
            ctx.stroke();
        }

        function stopDraw(e) {
            if (!drawing) return;
            e.preventDefault();
            drawing = false;
        }

        function clearSignature() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            setupCanvas();
            hasSignature = false;
            document.getElementById('signature_data').value = "";
        }

        function prepareSignature() {
            if (!hasSignature) {
                alert("Please sign before submitting.");
                return false;
            }
            document.getElementById('signature_data').value = canvas.toDataURL('image/png');
            return true;
        }

        async function loadUtilities() {
            const zip = document.getElementById('zip_code').value.trim();
            const state = document.getElementById('state').value.trim();
            const select = document.getElementById('utility_company');

            if (!state || zip.length !== 5) {
                select.innerHTML = '<option value="">Select state and enter a 5-digit ZIP code first</option><option value="OTHER">Other - I will type it below</option>';
                return;
            }

            select.innerHTML = '<option value="">Loading utilities...</option>';

            try {
                const response = await fetch(`/api/utilities?zip_code=${encodeURIComponent(zip)}&state=${encodeURIComponent(state)}`);
                const data = await response.json();

                if (!data.utilities || data.utilities.length === 0) {
                    select.innerHTML = '<option value="">No utilities found - type manually below</option><option value="OTHER">Other - I will type it below</option>';
                    return;
                }

                let html = '<option value="">Select Utility Company</option>';
                data.utilities.forEach(function(item) {
                    html += `<option value="${item}">${item}</option>`;
                });
                html += '<option value="OTHER">Other - I will type it below</option>';
                select.innerHTML = html;
            } catch (error) {
                select.innerHTML = '<option value="">Unable to load utilities - type manually below</option><option value="OTHER">Other - I will type it below</option>';
            }
        }

        window.addEventListener('load', function() {
            setupCanvas();
            loadUtilities();
        });

        window.addEventListener('resize', setupCanvas);

        document.getElementById('zip_code').addEventListener('input', function() {
            const zip = this.value.replace(/\\D/g, '').slice(0, 5);
            this.value = zip;

            const state = document.getElementById('state').value.trim();
            if (state && zip.length === 5) {
                loadUtilities();
            }
        });

        document.getElementById('zip_code').addEventListener('blur', loadUtilities);
        document.getElementById('state').addEventListener('change', function() {
            const zip = document.getElementById('zip_code').value.trim();
            if (this.value.trim() && zip.length === 5) {
                loadUtilities();
            }
        });

        canvas.addEventListener('mousedown', startDraw);
        canvas.addEventListener('mousemove', draw);
        canvas.addEventListener('mouseup', stopDraw);
        canvas.addEventListener('mouseleave', stopDraw);

        canvas.addEventListener('touchstart', startDraw, { passive: false });
        canvas.addEventListener('touchmove', draw, { passive: false });
        canvas.addEventListener('touchend', stopDraw, { passive: false });
    </script>
</body>
</html>
"""

SUCCESS_HTML = """
<!doctype html>
<html>
<head>
    <title>Submitted</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
""" + BASE_STYLES + """
</head>
<body>
    <div class="page">
        <div class="container">
            <div class="hero">
                <div class="logo-wrap">
{% if logo_exists %}
    <img src="{{ url_for('static', filename='SafetreetsLogo.png') }}" alt="SafeStreets Logo" class="brand-logo">
{% elif shield_exists %}
    <img src="{{ url_for('static', filename='shield.png') }}" alt="SafeStreets Shield" class="brand-shield">
{% else %}
    <div class="brand-fallback">SafeStreets LLC</div>
{% endif %}
                    <h1>Thank You</h1>
                    <p>Your Solar Consultation Agreement has been submitted.</p>
                </div>
            </div>

            <div class="card" style="text-align:center;">
                {% if shield_exists %}
                    <img src="{{ url_for('static', filename='shield.png') }}" alt="SafeStreets Shield" class="brand-shield">
                {% endif %}
                <h2 class="section-title">Submission Received</h2>
                <p class="section-subtitle">{{ message }}</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

# -------------------------------------------------
# PDF HELPERS
# -------------------------------------------------
def build_utility_options_html(zip_code="", state="", selected_utility=""):
    utilities = get_utility_options(zip_code, state)

    if not state or len(normalize_zip(zip_code)) != 5:
        options = ['<option value="">Select state and enter a 5-digit ZIP code first</option>']
    elif not utilities:
        options = ['<option value="">No utilities found - type manually below</option>']
    else:
        options = ['<option value="">Select Utility Company</option>']
        for utility in utilities:
            selected = "selected" if utility == selected_utility else ""
            options.append(f'<option value="{utility}" {selected}>{utility}</option>')

    other_selected = "selected" if selected_utility == "OTHER" else ""
    options.append(f'<option value="OTHER" {other_selected}>Other - I will type it below</option>')
    return "".join(options)

def clean_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9 _-]", "", value or "").strip()
    value = re.sub(r"\s+", " ", value)
    return value or "Customer"

def data_url_to_bytes(data_url: str) -> bytes:
    if "," not in data_url:
        raise ValueError("Invalid signature data.")
    return base64.b64decode(data_url.split(",", 1)[1])

def save_signature_image(signature_data: str, path: str):
    with open(path, "wb") as f:
        f.write(data_url_to_bytes(signature_data))

def wrap_lines(pdf, text, max_width, font_name="Helvetica", font_size=9):
    text = str(text or "").strip()
    if not text:
        return [""]

    words = text.split()
    lines = []
    current = ""

    pdf.setFont(font_name, font_size)

    for word in words:
        test = f"{current} {word}".strip()
        if pdf.stringWidth(test, font_name, font_size) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines

def draw_paragraph(pdf, text, x, y, max_width, font_name="Helvetica", font_size=9, line_height=12):
    pdf.setFont(font_name, font_size)
    lines = wrap_lines(pdf, text, max_width, font_name, font_size)
    for line in lines:
        pdf.drawString(x, y, line)
        y -= line_height
    return y

def build_pdf(data: dict, pdf_path: str, signature_data: str):
    pdf = canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter

    left_margin = 60
    right_margin = 60
    content_width = width - left_margin - right_margin

    logo_path = os.path.join(STATIC_FOLDER, LOGO_FILENAME)
    five_star_path = os.path.join(STATIC_FOLDER, FIVE_STAR_FILENAME)

    # HEADER
    if os.path.exists(logo_path):
        pdf.drawImage(
            logo_path,
            left_margin - 18,
            height - 78,
            width=185,
            height=40,
            mask='auto',
            preserveAspectRatio=True
        )

    if os.path.exists(five_star_path):
        pdf.drawImage(
            five_star_path,
            width - right_margin - 95,
            height - 68,
            width=95,
            height=20,
            mask='auto',
            preserveAspectRatio=True
        )

    pdf.setFillColorRGB(0, 0, 0)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawCentredString(width / 2, height - 55, "SafeStreet LLC")

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawCentredString(width / 2, height - 80, "Customer Solar Consultation Agreement")

    y = height - 120

    # DETAILS
    pdf.setFont("Helvetica", 9)
    details = [
        f"Customer Name: {data.get('customer_name', '')}",
        f"Phone Number: {data.get('phone_number', '')}",
        f"Email: {data.get('email', '')}",
        f"Street Address: {data.get('street_address', '')}",
        f"State: {data.get('state', '')}",
        f"ZIP Code: {data.get('zip_code', '')}",
        f"Utility Company: {data.get('utility_company', '')}",
        f"Requested Consultation Date: {data.get('consultation_date', '')}",
        f"Requested Consultation Time: {data.get('consultation_time', '')}",
        f"Submitted At (UTC): {data.get('submitted_at_utc', '')}",
        f"IP Address: {data.get('ip_address', '')}",
        f"Consent Version: {CONSENT_VERSION}",
    ]

    for line in details:
        pdf.drawString(left_margin, y, line)
        y -= 12

    y -= 26

    # CONSENT
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(left_margin, y, "Electronic Consent")
    y -= 22

    y = draw_paragraph(
        pdf,
        COMBINED_CONSENT_TEXT,
        left_margin,
        y,
        content_width,
        font_name="Helvetica",
        font_size=9,
        line_height=12
    )

    y -= 10

    y = draw_paragraph(
        pdf,
        "Privacy Policy, and Do Not Sell My Personal Information notice linked below.",
        left_margin,
        y,
        content_width,
        font_name="Helvetica",
        font_size=9,
        line_height=12
    )

    y -= 34

    # SIGNATURE
    if y < 220:
        pdf.showPage()
        y = height - 80

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(left_margin, y, "Customer Signature")
    y -= 28

    signature_png = os.path.join(TEMP_FOLDER, f"{data['base_name']}_signature.png")
    save_signature_image(signature_data, signature_png)

    # signature line
    line_y = y - 82
    pdf.line(left_margin, line_y, left_margin + 260, line_y)

    if os.path.exists(signature_png):
        pdf.drawImage(
            ImageReader(signature_png),
            left_margin + 5,
            y - 78,
            width=220,
            height=55,
            mask='auto'
        )

    y = line_y - 18

    pdf.setFont("Helvetica", 9)
    pdf.drawString(left_margin, y, f"Signed by: {data.get('customer_name', '')}")
    y -= 12
    pdf.drawString(left_margin, y, f"Signature Timestamp (UTC): {data.get('submitted_at_utc', '')}")

    pdf.setTitle(f"{data['customer_name']} - Solar Consultation Agreement")
    pdf.setAuthor("SafeStreet LLC")
    pdf.setSubject("Customer Solar Consultation Agreement")
    pdf.setCreator("SafeStreets Solar Consultation Form")

    pdf.save()
    return signature_png

def send_email(subject: str, body: str, pdf_path: str, bill_path: str | None):
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF was not created: {pdf_path}")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.set_content(body)

    with open(pdf_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="pdf",
            filename=os.path.basename(pdf_path)
        )

    if bill_path and os.path.exists(bill_path):
        mime_type, _ = mimetypes.guess_type(bill_path)
        if mime_type:
            maintype, subtype = mime_type.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"

        with open(bill_path, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype=maintype,
                subtype=subtype,
                filename=os.path.basename(bill_path)
            )

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        print(f"Sending email from {EMAIL_FROM} to {EMAIL_TO}")
        server.send_message(msg)
        print("Email send completed successfully")

def safe_delete(path):
    if path and os.path.exists(path):
        os.remove(path)

# -------------------------------------------------
# ROUTES
# -------------------------------------------------
@app.route("/", methods=["GET"])
def home():
    prefill = {
        "customer_name": "",
        "phone_number": "",
        "email": "",
        "street_address": "",
        "state": "",
        "zip_code": "",
        "consultation_date": "",
        "consultation_time": "",
        "utility_company_manual": "",
    }

    return render_template_string(
        FORM_HTML,
        error=None,
        prefill=prefill,
        state_options_html=render_state_options(""),
        utility_options_html=build_utility_options_html("", "", ""),
        combined_consent_text=COMBINED_CONSENT_TEXT,
        terms_url=TERMS_URL,
        privacy_url=PRIVACY_URL,
        do_not_sell_url=DO_NOT_SELL_URL,
        **common_template_context()
    )

@app.route("/api/utilities", methods=["GET"])
def api_utilities():
    zip_code = request.args.get("zip_code", "")
    state = request.args.get("state", "")
    utilities = get_utility_options(zip_code, state)

    matched_by = "none"
    normalized_zip = normalize_zip(zip_code)
    normalized_state = (state or "").upper().strip()

    if normalized_zip in UTILITY_ZIP_INDEX:
        matched_by = "zip"
    elif normalized_state in UTILITY_STATE_INDEX:
        matched_by = "state"

    return jsonify({
        "zip_code": normalized_zip,
        "state": normalized_state,
        "matched_by": matched_by,
        "count": len(utilities),
        "utilities": utilities
    })

@app.route("/qr.png", methods=["GET"])
def qr_png():
    root = request.url_root.rstrip("/") + "/"
    img = qrcode.make(root)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(buffer, mimetype="image/png")

@app.route("/submit", methods=["POST"])
def submit():
    customer_name = (request.form.get("customer_name") or "").strip()
    phone_number = (request.form.get("phone_number") or "").strip()
    email = (request.form.get("email") or "").strip()
    street_address = (request.form.get("street_address") or "").strip()
    state = (request.form.get("state") or "").strip()
    zip_code = (request.form.get("zip_code") or "").strip()
    consultation_date = (request.form.get("consultation_date") or "").strip()
    consultation_time = (request.form.get("consultation_time") or "").strip()
    signature_data = (request.form.get("signature_data") or "").strip()

    selected_utility = (request.form.get("utility_company") or "").strip()
    manual_utility = (request.form.get("utility_company_manual") or "").strip()

    if selected_utility and selected_utility != "OTHER":
        utility_company = selected_utility
    else:
        utility_company = manual_utility

    if not all([
        customer_name, phone_number, email, street_address,
        state, zip_code, utility_company, consultation_date,
        consultation_time, signature_data
    ]):
        prefill = {
            "customer_name": customer_name,
            "phone_number": phone_number,
            "email": email,
            "street_address": street_address,
            "state": state,
            "zip_code": zip_code,
            "consultation_date": consultation_date,
            "consultation_time": consultation_time,
            "utility_company_manual": manual_utility,
        }

        return render_template_string(
            FORM_HTML,
            error="Please complete all required fields, choose or type a utility company, and sign before submitting.",
            prefill=prefill,
            state_options_html=render_state_options(state),
            utility_options_html=build_utility_options_html(zip_code, state, selected_utility),
            combined_consent_text=COMBINED_CONSENT_TEXT,
            terms_url=TERMS_URL,
            privacy_url=PRIVACY_URL,
            do_not_sell_url=DO_NOT_SELL_URL,
            **common_template_context()
        )

    base_name = clean_name(customer_name).replace(" ", "_")
    submitted_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    ip_address = request.headers.get("X-Forwarded-For", request.remote_addr or "")

    data = {
        "customer_name": customer_name,
        "phone_number": phone_number,
        "email": email,
        "street_address": street_address,
        "state": state,
        "zip_code": zip_code,
        "utility_company": utility_company,
        "consultation_date": consultation_date,
        "consultation_time": consultation_time,
        "submitted_at_utc": submitted_at_utc,
        "ip_address": ip_address,
        "base_name": base_name,
    }

    pdf_filename = secure_filename(f"{clean_name(customer_name)} - Solar Consultation Agreement.pdf")
    pdf_path = os.path.join(TEMP_FOLDER, pdf_filename)

    bill_path = None
    signature_png = None
    email_sent = False

    try:
        signature_png = build_pdf(data, pdf_path, signature_data)

        electric_bill = request.files.get("electric_bill")
        if electric_bill and electric_bill.filename:
            extension = os.path.splitext(electric_bill.filename)[1]
            bill_filename = secure_filename(f"{base_name}_electric_bill{extension}")
            bill_path = os.path.join(TEMP_FOLDER, bill_filename)
            electric_bill.save(bill_path)

        # Exact email body requested
        subject = f"{customer_name} - Solar Consultation Agreement"
        body = (
            "Solar Team,\n\n"
            f"A new Solar Consultation Agreement has been submitted for {customer_name}.\n\n"
            "Please find the agreement attached and add it to the customer’s account in Salesforce.\n\n"
            "Let me know if you have any questions.\n\n"
            "Thank you,\n\n"
            "Gordon J. Black\n"
            "Sr. Manager Sales Operations | Solar\n"
            "Phone: (919) 773-7791\n"
            "Direct: (765) 993-7309\n"
            "Email: Gblack@safestreets.com"
        )

        if SEND_EMAIL:
            send_email(subject, body, pdf_path, bill_path)
            email_sent = True

        message = "Your Solar Consultation Agreement has been submitted successfully. A SafeStreets Solar leader will review your request."

    except Exception as e:
        print(f"Submission error: {e}")
        message = f"Email delivery test failed: {e}"

    finally:
        if SEND_EMAIL and DELETE_LOCAL_FILES_AFTER_EMAIL and email_sent:
            safe_delete(pdf_path)
            safe_delete(bill_path)
            safe_delete(signature_png)
            print("Temp files deleted after successful email send")

    return render_template_string(
        SUCCESS_HTML,
        message=message,
        **common_template_context()
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)