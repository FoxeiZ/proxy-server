const Reader = (function () {
  const galleryId = window.GALLERY_ID;
  const totalPages = window.TOTAL_PAGES;
  let currentPage = 1;

  function init() {
    if (!document.getElementById("readerImage")) {
      console.error("Reader elements not found in DOM");
      return;
    }

    setupElements();
    setupEventListeners();
    loadPage(1);
  }

  let readerImage,
    currentPageInput,
    prevBtn,
    nextBtn,
    prevArea,
    nextArea,
    loadingSpinner;

  function setupElements() {
    readerImage = document.getElementById("readerImage");
    currentPageInput = document.getElementById("currentPage");
    prevBtn = document.getElementById("prevBtn");
    nextBtn = document.getElementById("nextBtn");
    prevArea = document.getElementById("prevArea");
    nextArea = document.getElementById("nextArea");
    loadingSpinner = document.getElementById("loadingSpinner");
  }

  function loadPage(pageNum) {
    if (pageNum < 1) pageNum = 1;
    if (pageNum > totalPages) pageNum = totalPages;

    currentPage = pageNum;
    currentPageInput.value = pageNum;

    prevBtn.disabled = pageNum <= 1;
    nextBtn.disabled = pageNum >= totalPages;

    loadingSpinner.classList.add("active");
    readerImage.classList.add("loading");

    const newImage = new Image();
    newImage.onload = function () {
      readerImage.src = this.src;
      readerImage.classList.remove("loading");
      loadingSpinner.classList.remove("active");
    };
    newImage.onerror = function () {
      loadingSpinner.classList.remove("active");
      alert("Failed to load image");
    };

    newImage.src = `/galleries/chapter/${galleryId}/read/${pageNum}`;
  }

  let touchStartX = 0;
  let touchEndX = 0;

  function handleSwipe() {
    if (touchEndX < touchStartX - 50) {
      loadPage(currentPage + 1);
    }

    if (touchEndX > touchStartX + 50) {
      loadPage(currentPage - 1);
    }
  }

  function setupEventListeners() {
    prevBtn.addEventListener("click", () => loadPage(currentPage - 1));
    nextBtn.addEventListener("click", () => loadPage(currentPage + 1));

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
      if (isNaN(pageNum)) pageNum = 1;
      loadPage(pageNum);
    });

    document.addEventListener("touchstart", (e) => {
      touchStartX = e.changedTouches[0].screenX;
    });

    document.addEventListener("touchend", (e) => {
      touchEndX = e.changedTouches[0].screenX;
      handleSwipe();
    });
  }

  return {
    init: init,
  };
})();

window.addEventListener("load", function () {
  Reader.init();
});
