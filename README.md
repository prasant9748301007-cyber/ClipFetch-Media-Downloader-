# 🚀 ClipFetch

### Download Your Media, Your Way.

ClipFetch is a modern media downloader built with **Python FastAPI, HTML5, CSS3, and JavaScript**. It provides a single platform for downloading authorized video and audio content from TikTok, Facebook, and Instagram.

## ✨ Features

- 📹 **Multi-Platform Support** – TikTok, Facebook, and Instagram.
- 🎬 **Video Download** – Download supported videos in MP4 format.
- 🎵 **Audio Download** – Extract and download audio in MP3 format.
- 🔗 **URL-Based Download** – Paste a supported media URL.
- 🖥️ **Modern UI** – Clean and intuitive user interface.
- 📱 **Responsive Design** – Supports different screen sizes.
- ⚡ **Fast Backend** – Powered by Python FastAPI.
- 🧪 **Automated Testing** – Tests using Pytest.

## 🛠️ Technology Stack

| Technology | Purpose |
|---|---|
| Python | Backend programming |
| FastAPI | Backend API framework |
| Pydantic Settings | Application configuration |
| HTML5 | Frontend structure |
| CSS3 | Styling and responsive design |
| JavaScript | Frontend functionality |
| yt-dlp | Media extraction |
| curl-cffi | HTTP client support |
| FFmpeg | Media conversion |
| Pytest | Automated testing |
| Git & GitHub | Version control |

## 📂 Project Structure

```text
ClipFetch/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── routes.py
│   │   ├── database/
│   │   ├── schemas/
│   │   ├── services/
│   │   │   ├── download_service.py
│   │   │   ├── media_service.py
│   │   │   └── platform_detector.py
│   │   └── main.py
│   │
│   ├── temp/
│   ├── .env.example
│   └── requirements.txt
│
├── frontend/
│   ├── css/
│   │   └── style.css
│   ├── js/
│   │   ├── app.js
│   │   └── config.js
│   ├── index.html
│   ├── privacy.html
│   └── terms.html
│
├── tests/
│   ├── test_api.py
│   ├── test_download_service.py
│   └── test_platform_detector.py
│
├── .gitignore
├── LICENSE
└── README.md
```

## ⚙️ Installation and Setup

Follow these steps to run ClipFetch locally.

### 1. Clone the Repository

```bash
git clone https://github.com/prasant9748301007-cyber/ClipFetch-Media-Downloader-.git ClipFetch
cd ClipFetch
```

### 2. Install FFmpeg

For Ubuntu or WSL:

```bash
sudo apt update
sudo apt install ffmpeg
```

Verify installation:

```bash
ffmpeg -version
```

### 3. Create a Virtual Environment

```bash
cd backend
python3 -m venv venv
```

### 4. Activate the Virtual Environment

```bash
source venv/bin/activate
```

### 5. Install Dependencies

```bash
pip install -r requirements.txt
```

For running automated tests, install Pytest if it is not already installed:

```bash
pip install pytest
```

### 6. Start the Backend Server

From the backend directory:

```bash
uvicorn app.main:app --reload
```

Backend URL:

http://127.0.0.1:8000

Interactive API Documentation:

http://127.0.0.1:8000/docs

### 7. Start the Frontend

Open a second terminal:

```bash
cd ~/ClipFetch/frontend
python3 -m http.server 5500
```

Open your browser:

http://127.0.0.1:5500

## 🔌 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | General application endpoint |
| GET | `/api/health` | Check backend health |
| POST | `/api/validate-url` | Validate media URL |
| POST | `/api/metadata` | Retrieve media information |
| POST | `/api/download` | Download supported media |

For request parameters and response details, visit:

http://127.0.0.1:8000/docs

## 🧪 Running Tests

From the backend directory with the virtual environment activated:

```bash
python -m pytest ../tests -v
```

## 🔒 Responsible Usage

ClipFetch is intended for downloading media that users own or are authorized to download.

- Respect copyright and intellectual property rights.
- Follow the terms of service of supported platforms.
- Do not download or redistribute unauthorized content.
- Use the application responsibly.

## 🚀 Future Improvements

- Public cloud deployment.
- Improved download management.
- Additional media format options.
- Enhanced error handling.
- Performance optimization.

## 👨‍💻 Developer

**ClipFetch** is a full-stack development project created to explore Python backend development, API integration, frontend development, and media processing.

---

⭐ If you find this project interesting, consider giving the repository a star!

**Built with ❤️ using Python and JavaScript.**
