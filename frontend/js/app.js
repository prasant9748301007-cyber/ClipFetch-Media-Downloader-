// ========================================
// CLIPFETCH - FRONTEND / BACKEND INTEGRATION
// ========================================

const API_BASE_URL = (
    window.CLIPFETCH_CONFIG?.API_BASE_URL ||
    "http://127.0.0.1:8000"
).replace(/\/+$/, "");

// HTML elements
const form = document.getElementById("fetchForm");
const urlInput = document.getElementById("urlInput");
const fetchButton = document.getElementById("fetchBtn");

const statusMessage = document.getElementById("statusMessage");
const downloadStatus = document.getElementById("downloadStatus");

const resultCard = document.getElementById("mediaResult");
const mediaThumbnail = document.getElementById("thumbnail");
const mediaTitle = document.getElementById("mediaTitle");
const mediaDescription = document.getElementById("mediaDescription");

const mediaDuration = document.getElementById("mediaDuration");
const mediaResolution = document.getElementById("mediaResolution");
const mediaFormat = document.getElementById("mediaFormat");

const formatOptions = document.getElementById("formatOptions");
const downloadButton = document.getElementById("downloadBtn");

// Current media information
let currentMediaURL = "";
let currentMedia = null;


// ========================================
// STATUS MESSAGES
// ========================================

function showStatus(message, type = "") {
    statusMessage.textContent = message;
    statusMessage.className = "status-message";

    if (type) {
        statusMessage.classList.add(type);
    }

    statusMessage.hidden = false;
}

function hideStatus() {
    statusMessage.hidden = true;
    statusMessage.textContent = "";
}

function showDownloadStatus(message, type = "") {
    downloadStatus.textContent = message;
    downloadStatus.className = "status-message";

    if (type) {
        downloadStatus.classList.add(type);
    }

    downloadStatus.hidden = false;
}

function hideDownloadStatus() {
    downloadStatus.hidden = true;
    downloadStatus.textContent = "";
}


// ========================================
// LOADING STATE
// ========================================

function setFetching(isLoading) {
    fetchButton.disabled = isLoading;
    urlInput.disabled = isLoading;

    fetchButton.textContent = isLoading
        ? "Fetching Media..."
        : "⌕ Fetch Media";
}

function setDownloading(isLoading) {
    downloadButton.disabled = isLoading;

    downloadButton.textContent = isLoading
        ? "Downloading..."
        : "⇩ Download Media";
}


// ========================================
// RESET MEDIA RESULT
// ========================================

function clearMediaResult() {
    currentMediaURL = "";
    currentMedia = null;

    resultCard.hidden = true;

    mediaThumbnail.hidden = true;
    mediaThumbnail.removeAttribute("src");

    mediaTitle.textContent = "";
    mediaDescription.textContent = "";

    mediaDuration.textContent = "--";
    mediaResolution.textContent = "--";
    mediaFormat.textContent = "--";

    formatOptions.replaceChildren();

    downloadButton.disabled = true;

    hideDownloadStatus();
}


// ========================================
// FORMAT DURATION
// ========================================

function formatDuration(seconds) {
    if (
        typeof seconds !== "number" ||
        !Number.isFinite(seconds) ||
        seconds < 0
    ) {
        return "Not available";
    }

    const totalSeconds = Math.floor(seconds);

    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const remainingSeconds = totalSeconds % 60;

    if (hours > 0) {
        return `${hours}:${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
    }

    return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
}


// ========================================
// SAFE THUMBNAIL VALIDATION
// ========================================

function isSafeThumbnailURL(value) {
    if (typeof value !== "string") {
        return false;
    }

    try {
        const parsed = new URL(value);

        return parsed.protocol === "https:" &&
            Boolean(parsed.hostname);
    } catch {
        return false;
    }
}


// ========================================
// CREATE FORMAT OPTIONS
// ========================================

function createFormatOptions(formats) {
    formatOptions.replaceChildren();

    // Default best-quality option
    const bestOption = document.createElement("label");
    bestOption.className = "format-option";

    bestOption.innerHTML = `
        <input
            type="radio"
            name="format_id"
            value="best"
            checked
        >
        <span class="radio-circle"></span>
        <span class="format-info">
            <strong>Best Quality <small>(Recommended)</small></strong>
            <small>Best available quality</small>
        </span>
        <span class="quality-tag">HD</span>
    `;

    formatOptions.appendChild(bestOption);

        // Audio-only MP3 option
    const audioOption = document.createElement("label");
    audioOption.className = "format-option";

    audioOption.innerHTML = `
        <input
            type="radio"
            name="format_id"
            value="audio_mp3"
        >
        <span class="radio-circle"></span>
        <span class="format-info">
            <strong>Audio Only · MP3</strong>
            <small>MP3 · Audio only · 192 kbps</small>
        </span>
        <span class="quality-tag">MP3</span>
    `;

    formatOptions.appendChild(audioOption);

if (!Array.isArray(formats)) {
        return;
    }

    // Keep only valid video formats with known resolution
    const validFormats = formats.filter((format) => {
        return (
            format &&
            typeof format.format_id === "string" &&
            /^[A-Za-z0-9_-]{1,40}$/.test(format.format_id) &&
            format.has_video === true &&
            typeof format.resolution === "string" &&
            /^\d+x\d+$/.test(format.resolution)
        );
    });

    // Sort by quality/bitrate, highest first
    validFormats.sort((a, b) => {
        const bitrateA = Number(a.tbr) || 0;
        const bitrateB = Number(b.tbr) || 0;

        if (bitrateA !== bitrateB) {
            return bitrateB - bitrateA;
        }

        const sizeA = Number(a.filesize) || Number(a.filesize_approx) || 0;
        const sizeB = Number(b.filesize) || Number(b.filesize_approx) || 0;

        return sizeB - sizeA;
    });

    // Keep only one format for each resolution
    const uniqueFormats = new Map();

    validFormats.forEach((format) => {
        const resolution = format.resolution;

        if (!uniqueFormats.has(resolution)) {
            uniqueFormats.set(resolution, format);
        }
    });

    // Display highest resolution first
    const finalFormats = [...uniqueFormats.values()].sort((a, b) => {
        const [widthA, heightA] = a.resolution.split("x").map(Number);
        const [widthB, heightB] = b.resolution.split("x").map(Number);

        return (widthB * heightB) - (widthA * heightA);
    });

    finalFormats.forEach((format) => {
        const label = document.createElement("label");
        label.className = "format-option";

        const radio = document.createElement("input");
        radio.type = "radio";
        radio.name = "format_id";
        radio.value = format.format_id;

        const circle = document.createElement("span");
        circle.className = "radio-circle";

        const info = document.createElement("span");
        info.className = "format-info";

        const heading = document.createElement("strong");
        heading.textContent = `${format.resolution} · MP4`;

        const description = document.createElement("small");
        description.textContent = "MP4 · Video";

        info.appendChild(heading);
        info.appendChild(description);

        const tag = document.createElement("span");
        tag.className = "quality-tag";

        const [width, height] = format.resolution.split("x").map(Number);
        tag.textContent = `${Math.min(width, height)}p`;

        label.appendChild(radio);
        label.appendChild(circle);
        label.appendChild(info);
        label.appendChild(tag);

        formatOptions.appendChild(label);
    });
}


// ========================================
// DISPLAY MEDIA METADATA
// ========================================

function displayMetadata(media) {
    currentMedia = media;

    resultCard.hidden = false;

    mediaTitle.textContent =
        media.title || "Title not available";

    mediaDescription.textContent =
        `${media.platform || "Unknown platform"} · ${media.uploader || "Creator not available"}`;

    mediaDuration.textContent =
        formatDuration(media.duration);

    const formats = Array.isArray(media.formats)
        ? media.formats
        : [];

    const firstVideoFormat = formats.find(
        (format) => format.has_video
    );

    const firstFormat = firstVideoFormat || formats[0];

    mediaResolution.textContent =
        firstFormat?.resolution || "Not available";

    mediaFormat.textContent =
        firstFormat?.extension || "Not available";

    // Display thumbnail
    if (isSafeThumbnailURL(media.thumbnail)) {
        mediaThumbnail.src = media.thumbnail;

        mediaThumbnail.alt = media.title
            ? `Thumbnail for ${media.title}`
            : "Media thumbnail";

        mediaThumbnail.hidden = false;

        mediaThumbnail.onerror = () => {
            mediaThumbnail.hidden = true;
            mediaThumbnail.removeAttribute("src");
        };
    } else {
        mediaThumbnail.hidden = true;
    }

    // Display available download formats
    createFormatOptions(formats);

    downloadButton.disabled = false;

    resultCard.scrollIntoView({
        behavior: "smooth",
        block: "start"
    });

    showStatus("Media information retrieved successfully.", "success");
}


// Clear stale metadata if the user edits the source URL after fetching it.
urlInput.addEventListener("input", () => {
    if (currentMediaURL && urlInput.value.trim() !== currentMediaURL) {
        clearMediaResult();
        hideStatus();
    }
});


// ========================================
// FETCH MEDIA METADATA
// ========================================

form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const url = urlInput.value.trim();

    hideStatus();
    hideDownloadStatus();
    clearMediaResult();

    if (!url) {
        showStatus("Please enter a media URL.", "error");
        return;
    }

    let parsedURL;

    try {
        parsedURL = new URL(url);
    } catch {
        showStatus("Please enter a valid URL.", "error");
        return;
    }

    if (parsedURL.protocol !== "https:") {
        showStatus("Please enter an HTTPS URL.", "error");
        return;
    }

    setFetching(true);
    showStatus("Checking media availability...");

    try {
        const response = await fetch(
            `${API_BASE_URL}/api/metadata`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ url })
            }
        );

        const data = await response.json();

        if (!response.ok) {
            const message =
                typeof data.detail === "string"
                    ? data.detail
                    : "Unable to retrieve media information.";

            throw new Error(message);
        }

        if (!data.success || !data.media) {
            throw new Error(
                "The server returned an unexpected response."
            );
        }

        currentMediaURL = url;

        displayMetadata(data.media);

    } catch (error) {
        console.error("ClipFetch metadata error:", error);

        showStatus(
            error.message || "Unable to retrieve media metadata.",
            "error"
        );

    } finally {
        setFetching(false);
    }
});


// ========================================
// DOWNLOAD MEDIA
// ========================================

downloadButton.addEventListener("click", async () => {
    const url = currentMediaURL;

    const selectedFormat = document.querySelector(
        'input[name="format_id"]:checked'
    );

    const formatId = selectedFormat?.value || "best";

    if (!url || !currentMedia) {
        showDownloadStatus(
            "Please fetch media information first.",
            "error"
        );
        return;
    }

    setDownloading(true);
    showDownloadStatus("Preparing your download. Please wait...");
    const waitMessageTimer = window.setTimeout(() => {
        showDownloadStatus(
            "The server is still preparing the file. The browser download starts when preparation is complete."
        );
    }, 10000);

    try {
        const response = await fetch(
            `${API_BASE_URL}/api/download`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    url: url,
                    format_id: formatId
                })
            }
        );

        if (!response.ok) {
            let message = "Download failed. Please try again.";

            try {
                const data = await response.json();

                if (typeof data.detail === "string") {
                    message = data.detail;
                }
            } catch {
                // Keep default error message.
            }

            throw new Error(message);
        }

        const blob = await response.blob();

        if (blob.size === 0) {
            throw new Error("The server returned an empty file.");
        }

        // Get filename from response headers
    const disposition = response.headers.get("Content-Disposition");

    let filename = "clipfetch_media.mp4";

    if (disposition) {
    const utf8Match = disposition.match(
        /filename\*=UTF-8''([^;]+)/i
    );

    const normalMatch = disposition.match(
        /filename="?([^";]+)"?/i
    );

    const rawFilename = utf8Match?.[1]
        ? decodeURIComponent(utf8Match[1].trim())
        : normalMatch?.[1]?.trim();

    if (rawFilename) {
        filename = rawFilename;
    }
   }

// Ensure the downloaded file has a video extension
if (!/\.[a-z0-9]{2,5}$/i.test(filename)) {
    filename += ".mp4";
}
        // Create downloadable file
        const objectURL = URL.createObjectURL(blob);

        const link = document.createElement("a");
        link.href = objectURL;
        link.download = filename;

        document.body.appendChild(link);
        link.click();
        link.remove();

        setTimeout(() => {
            URL.revokeObjectURL(objectURL);
        }, 10000);

        showDownloadStatus(
            "Your download has started successfully.",
            "success"
        );

    } catch (error) {
        console.error("ClipFetch download error:", error);

        showDownloadStatus(
            error.message || "Unable to download media.",
            "error"
        );

    } finally {
        window.clearTimeout(waitMessageTimer);
        setDownloading(false);
    }
});


// ========================================
// INITIAL PAGE STATE
// ========================================

clearMediaResult();
hideStatus();
hideDownloadStatus();

// ========================================
// DARK / LIGHT THEME TOGGLE
// ========================================

const themeToggle = document.getElementById("themeToggle");

// Load previously saved theme
const savedTheme = localStorage.getItem("clipfetch-theme");

if (savedTheme === "dark") {
    document.body.classList.add("dark-theme");
}

// Update theme button icon
function updateThemeIcon() {
    const isDark = document.body.classList.contains("dark-theme");

    themeToggle.textContent = isDark ? "☀" : "☾";

    themeToggle.setAttribute("aria-pressed", String(isDark));
}

// Toggle theme on button click
themeToggle.addEventListener("click", () => {
    const isDark = document.body.classList.toggle("dark-theme");

    localStorage.setItem(
        "clipfetch-theme",
        isDark ? "dark" : "light"
    );

    updateThemeIcon();
});

// Set correct icon when page loads
updateThemeIcon();
