# ClipFetch 🚀

**Download Your Media, Your Way.**

ClipFetch is a modern, user-friendly media downloader built with Python FastAPI, HTML, CSS, and JavaScript. It allows users to download authorized video and audio content from TikTok, Facebook, and Instagram through a single platform.

## ✨ Features

- 📹 **Multi-Platform Support:** Supports TikTok, Facebook, and Instagram.
- 🎬 **Video Download:** Download videos in MP4 format.
- 🎵 **Audio Download:** Extract and download audio in MP3 format.
- 🔗 **URL-Based Download:** Simply paste a supported media URL.
- 📱 **Responsive UI:** Modern interface designed for different screen sizes.
- ⚡ **Fast Backend:** Powered by Python FastAPI.
- 🎨 **Modern Design:** Clean and user-friendly interface.
- 🧪 **API Testing:** Includes automated tests using Pytest.

## 🛠️ Technologies Used

| Technology | Purpose |
|---|---|
| Python | Backend programming |
| FastAPI | Backend API framework |
| HTML5 | Frontend structure |
| CSS3 | UI design and styling |
| JavaScript | Frontend functionality |
| yt-dlp | Media extraction |
| FFmpeg | Media conversion |
| SQLite | Database |
| Pytest | Automated testing |
| Git & GitHub | Version control |

## 📂 Project Structure

```text
ClipFetch/
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   └── services/
│   ├── tests/
│   └── requirements.txt
│
├── frontend/
│   ├── index.html
│   ├── css/
│   └── js/
│
├── tests/
├── .gitignore
└── README.md
```

## ⚙️ Installation and Setup

### 1. Clone the Repository

```bash
git clone https://github.com/prasant9748301007-cyber/ClipFetch-Media-Downloader-.git
cd ClipFetch-Media-Downloader-
```

### 2. Create a Virtual Environment

```bash
cd backend
python3 -m venv venv
```

### 3. Activate Virtual Environment

```bash
source venv/bin/activate
```

### 4. Install Dependencies

```bash
pip install -r requirements.txt
```

### 5. Install FFmpeg

For Ubuntu:

```bash
sudo apt update
sudo apt install ffmpeg
```

### 6. Start the Backend Server

From the backend directory:

```bash
uvicorn app.main:app --reload
```

Backend runs at:

`http://127.0.0.1:8000`

API documentation:

`http://127.0.0.1:8000/docs`

### 7. Start the Frontend

Open another terminal:

```bash
cd ~/ClipFetch/frontend
python3 -m http.server 5500
```

Open in your browser:

`http://127.0.0.1:5500`

## 🧪 Run Tests

From the backend directory:

```bash
python -m pytest ../tests -v
```

## 🔒 Responsible Usage

ClipFetch is intended for downloading media that users own or are authorized to download.

- Respect copyright and intellectual property rights.
- Follow the terms of service of supported platforms.
- Do not use the application to download or redistribute unauthorized content.

## 🚀 Future Improvements

- Cloud deployment.
- Improved download management.
- Additional media format options.
- Better error handling.
- Performance optimization.

## 👨‍💻 Developer

**ClipFetch** — A personal full-stack development project built to explore Python backend development, API integration, frontend design, and media processing.

---

⭐ If you find this project interesting, consider giving the repository a star!

**Built with ❤️ using Python and JavaScript.**
