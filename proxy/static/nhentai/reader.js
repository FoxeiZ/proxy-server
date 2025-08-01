// @ts-check

const Reader = (function () {
  // @ts-ignore
  const galleryId = window.GALLERY_ID;
  // @ts-ignore
  const totalPages = window.TOTAL_PAGES;
  // @ts-ignore
  const nextChapterId = window.NEXT_CHAPTER;
  // @ts-ignore
  const prevChapterId = window.PREV_CHAPTER;
  let currentPage = 1;

  function init() {
    if (!document.getElementById("readerImage")) {
      console.error("Reader elements not found in DOM");
      return;
    }

    setupElements();
    createNotification();
    setupEventListeners();
    loadPage(1);
  }

  let readerImage, currentPageInput, prevArea, nextArea, loadingSpinner;
  let chapterNotification = null;
  let notificationTimeout = null;

  function setupElements() {
    readerImage = document.getElementById("readerImage");
    currentPageInput = document.getElementById("currentPage");
    prevArea = document.getElementById("prevArea");
    nextArea = document.getElementById("nextArea");
    loadingSpinner = document.getElementById("loadingSpinner");
  }

  function createNotification() {
    chapterNotification = document.createElement("div");
    chapterNotification.className = "chapter-notification";
    document.body.appendChild(chapterNotification);
  }

  function showNotification(message, duration = 3000) {
    if (notificationTimeout) {
      clearTimeout(notificationTimeout);
    }

    chapterNotification.innerHTML = message;
    chapterNotification.classList.add("show");

    notificationTimeout = setTimeout(() => {
      chapterNotification.classList.remove("show");
    }, duration);
  }

  function navigateToChapter(chapterId) {
    window.location.href = `/galleries/chapter/${chapterId}/read`;
  }

  function loadPage(pageNum) {
    if (pageNum < 1) {
      if (prevChapterId) {
        navigateToChapter(prevChapterId);
        return;
      }
      pageNum = 1;
    } else if (pageNum > totalPages) {
      if (nextChapterId) {
        navigateToChapter(nextChapterId);
        return;
      }
      pageNum = totalPages;
    }

    currentPage = pageNum;
    currentPageInput.value = pageNum;

    if (pageNum === 1 && prevChapterId) {
      showNotification(
        'This is the first page. <span class="action">Previous chapter available</span>'
      );
    } else if (pageNum === totalPages && nextChapterId) {
      showNotification(
        'This is the last page. <span class="action">Next chapter available</span>'
      );
    }

    prevArea.classList.toggle("disabled", pageNum <= 1);
    nextArea.classList.toggle("disabled", pageNum >= totalPages);

    loadingSpinner.classList.add("active");
    readerImage.classList.add("loading");

    const newImage = new Image();
    newImage.onload = function () {
      readerImage.src = newImage.src;
      readerImage.classList.remove("loading");
      loadingSpinner.classList.remove("active");
    };
    newImage.onerror = function () {
      loadingSpinner.classList.remove("active");
      alert("Failed to load image");
    };

    newImage.src = `/galleries/chapter/${galleryId}/read/${pageNum}`;
  }

  function setupEventListeners() {
    prevArea.addEventListener("click", () => loadPage(currentPage - 1));
    nextArea.addEventListener("click", () => loadPage(currentPage + 1));

    document.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") {
        loadPage(currentPage - 1);
      } else if (e.key === "ArrowRight") {
        loadPage(currentPage + 1);
      }
    });

    currentPageInput.addEventListener("change", () => {
      let pageNum = parseInt(currentPageInput.value);
      if (Number.isNaN(pageNum)) {
        pageNum = 1;
      }
      loadPage(pageNum);
    });
  }

  return {
    init: init,
  };
})();

window.addEventListener("load", function () {
  Reader.init();
});
