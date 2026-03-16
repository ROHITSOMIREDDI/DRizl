# Drizl — Premium URL Shortener & Bio Page Builder

Drizl is a full-stack URL shortening platform built with Flask, featuring advanced analytics, A/B testing, geo-targeting, and customizable bio pages.

## ✨ Features

- **High-Speed Redirects**: Optimized with Redis caching for sub-100ms response times.
- **Advanced Targeting**: Route users based on their country, device (mobile/desktop), or platform.
- **A/B Visualizer**: Multi-variant URL testing with real-time leading variant tracking.
- **Rich Analytics**: Deep dive into click patterns with SVG-rendered time series, country breakdowns, and device distribution.
- **Social Bio Pages**: Create beautiful, personalized landing pages for your social profile links.
- **Security First**: Password-protected links, click-expiring links, and date-based TTL.
- **Developer API**: RESTful API v1 for programmatic link management with secure API keys.
- **QR Codes**: On-demand high-quality QR code generation for every link.

## 🛠 Tech Stack

- **Backend**: Python 3.13, Flask 3.0.3, SQLAlchemy 2.0.48
- **Frontend**: HTML5, Jinja2, Vanilla CSS (Premium Dark Mode design)
- **Database**: PostgreSQL (Production) / SQLite (Local)
- **Caching**: Redis
- **Analytics**: GeoIP2, User-Agents
- **Security**: Bcrypt, JWT authentication, Flask-Limiter

## 🚀 Local Setup

1. **Prerequisites**:
   - Python 3.13+ installed.
   - (Optional) Redis server running locally.

2. **Quick Start**:
   Double-click `setup.bat` or run:
   ```bash
   python -m venv venv
   call venv\Scripts\activate.bat
   pip install -r requirements.txt
   python -c "from app import db, app; app.app_context().push(); db.create_all()"
   ```

3. **Run the App**:
   ```bash
   python app.py
   ```
   Open `http://localhost:5000` in your browser.

## 📝 Credits

Developed by **Rohit Somireddi** as a Full Stack Development project.
Built for performance, scalability, and premium aesthetics.

---
© 2026 Drizl. All Rights Reserved.
