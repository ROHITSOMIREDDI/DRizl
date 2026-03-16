# 🌧️ Drizl — Premium URL Shortener & Analytics

Drizl is a full-stack, high-performance URL shortening platform designed for both speed and aesthetics. It empowers users to create shortened links with advanced targeting rules, real-time analytics, and personalized bio pages.

---

## 🚀 Vision

**Drizl** isn't just another URL shortener; it's a complete link-management suite. Whether you're a marketer tracking campaigns or a developer building with our REST API, Drizl provides the tools you need in a sleek, premium interface.

## ✨ Key Features

### 🔗 Link Management
- **Custom Slugs**: Brand your links with custom text.
- **QR Code Generation**: Instantly generate high-quality QR codes for every short link.
- **Password Protection**: Secure your links with end-to-end password gates.
- **Link Expiry**: Set TTL (Time to Live) based on dates or total click counts.

### 🎯 Advanced Targeting
- **Geo-Targeting**: Redirect users based on their country (powered by GeoIP2).
- **Device Targeting**: Route traffic differently for Mobile, Desktop, or Tablet users.
- **A/B Testing**: Randomly split traffic between two variants and track the winner in real-time.

### 📊 Deep Analytics
- **Live Visualizations**: Beautiful SVG-rendered charts for daily click trends.
- **Demographics**: Breakdown by Top Countries, Browser types, and Device platforms.
- **Referrer Tracking**: See exactly where your traffic is coming from.

### 📱 Social Bio Pages
- **Custom Bio Sections**: Create a personalized "Link-in-bio" page with custom icons and featured links.
- **Visual Editor**: Real-time management of your public profile.

### 🛠 Developer API (v1)
- **Rest API**: Programmatically shorten, list, and delete links.
- **Secure Keys**: Manage multiple API keys with granular revocation.

---

## 💻 Tech Stack

- **Backend**: Python 3.13, Flask (Unified Full-Stack)
- **Database**: SQLAlchemy (PostgreSQL/SQLite)
- **Caching**: Redis (High-speed redirect fallbacks)
- **Styling**: Vanilla CSS (Custom Design System with Premium Dark Mode)
- **Security**: Bcrypt Hashing, JWT Session Management, Rate Limiting

---

## 🛠 Installation & Local Setup

Drizl is designed to be easy to get running.

1. **Clone the repository**:
   ```bash
   git clone https://github.com/ROHITSOMIREDDI/DRizl.git
   cd DRizl
   ```

2. **Run the setup script**:
   We've included a `setup.bat` for Windows users to automate the virtual environment and dependency installation.
   ```bash
   setup.bat
   ```

3. **Manual Setup** (if not on Windows):
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python -c "from app import db, app; app.app_context().push(); db.create_all()"
   ```

4. **Launch the platform**:
   ```bash
   python app.py
   ```
   *Platform will be live at `http://127.0.0.1:5000`*

---

## 👤 Credits

Developed with ❤️ by **Rohit Somireddi** as a Full Stack Development Project.
Built for performance, scalability, and premium aesthetics.

© 2026 Drizl. All Rights Reserved.
