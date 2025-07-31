let progressInterval = null;

function addGallery(event, galleryId) {
    event.preventDefault();
    const addButton = document.getElementById("add");

    addButton.classList.add("btn-disabled");
    addButton.innerText = "Adding...";

    fetch(`/func/add/${galleryId}`, {
        method: 'GET',
        headers: {
            'Content-Type': 'application/json',
        },
    }).then(response => response.json())
        .then(data => {
            if (data.message === "Download started" || data.message === "Gallery is already being downloaded") {
                addButton.innerText = "Pending...";
                showCancelButton(galleryId);
                startProgressMonitoring(galleryId);
            } else {
                alert('Failed to add gallery.');
                addButton.classList.remove("btn-disabled");
                addButton.innerText = "Add";
            }
        }).catch(error => {
            console.error('Error:', error);
            alert('Failed to add gallery.');
            addButton.classList.remove("btn-disabled");
            addButton.innerText = "Add";
        });
}

function cancelDownload(event, galleryId) {
    event.preventDefault();
    const cancelButton = document.getElementById("cancel");

    cancelButton.classList.add("btn-disabled");
    cancelButton.innerText = "Cancelling...";

    fetch(`/func/cancel/${galleryId}`, {
        method: 'GET',
        headers: {
            'Content-Type': 'application/json',
        },
    }).then(response => response.json())
        .then(data => {
            if (data.message === "Download cancelled successfully") {
                const addButton = document.getElementById("add");
                addButton.classList.remove("btn-disabled");
                addButton.innerText = "Add";
                hideCancelButton();
                stopProgressMonitoring();
            } else {
                alert('Failed to cancel download.');
                cancelButton.classList.remove("btn-disabled");
                cancelButton.innerText = "Cancel";
            }
        }).catch(error => {
            console.error('Error:', error);
            alert('Failed to cancel download.');
            cancelButton.classList.remove("btn-disabled");
            cancelButton.innerText = "Cancel";
        });
}

function showCancelButton(galleryId) {
    let cancelButton = document.getElementById("cancel");
    if (!cancelButton) {
        const addButton = document.getElementById("add");
        cancelButton = document.createElement("a");
        cancelButton.id = "cancel";
        cancelButton.className = "btn btn-secondary";
        cancelButton.style.cssText = "min-width: unset; padding: 0 0.75rem; margin-left: 0.5rem;";
        cancelButton.href = "#";
        cancelButton.onclick = (event) => cancelDownload(event, galleryId);
        cancelButton.innerHTML = 'Cancel <i class="fa fa-times"></i>';
        addButton.parentNode.insertBefore(cancelButton, addButton.nextSibling);
    }
    cancelButton.style.display = "inline-block";
}

function hideCancelButton() {
    const cancelButton = document.getElementById("cancel");
    if (cancelButton) {
        cancelButton.style.display = "none";
    }
}

function startProgressMonitoring(galleryId) {
    if (progressInterval) {
        clearInterval(progressInterval);
    }

    updateProgress(galleryId);
    progressInterval = setInterval(() => updateProgress(galleryId), 1000);
}

function updateProgress(galleryId) {
    fetch(`/func/progress/${galleryId}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                // No progress found - check if download completed
                fetch(`/p/nhentai.net/g/${galleryId}`)
                    .then(() => {
                        // If we can fetch the page, check file status by reloading
                        const addButton = document.getElementById("add");
                        addButton.innerText = "Added";
                        stopProgressMonitoring();
                        setTimeout(() => {
                            window.location.reload();
                        }, 1000);
                    })
                    .catch(() => {
                        // Something went wrong
                        stopProgressMonitoring();
                    });
                return;
            }

            const addButton = document.getElementById("add");
            const percentage = Math.round(data.progress_percentage);

            switch (data.status) {
                case "pending":
                    addButton.innerText = "Pending...";
                    break;
                case "downloading":
                    addButton.innerText = `Downloading...${percentage}%`;
                    break;
                case "failed":
                    addButton.innerText = `Failed (${data.failed_images}/${data.total_images})`;
                    addButton.style.color = "#ff6b6b";
                    stopProgressMonitoring();
                    break;
                case "cancelled":
                    addButton.innerText = "Cancelled";
                    addButton.style.color = "#ff6b6b";
                    stopProgressMonitoring();
                    break;
                case "missing":
                    addButton.innerText = `Missing (${data.failed_images}/${data.total_images})`;
                    addButton.style.color = "#ff6b6b";
                    stopProgressMonitoring();
                    break;
                default:
                    addButton.innerText = "Add";
                    addButton.style.color = "";
                    stopProgressMonitoring();
                    break;
            }
        })
        .catch(error => {
            console.error('Progress fetch error:', error);
        });
}

function stopProgressMonitoring() {
    if (progressInterval) {
        clearInterval(progressInterval);
        progressInterval = null;
    }
}
function setupFloatingMenuToggle() {
    const menuToggle = document.getElementById('floatingMenuToggle');
    const menuList = document.getElementById('floatingMenuList');
    if (!menuToggle || !menuList) return;

    menuToggle.addEventListener('click', function (e) {
        e.stopPropagation();
        menuList.classList.toggle('show');
    });

    document.addEventListener('click', function (e) {
        if (!menuToggle.contains(e.target) && !menuList.contains(e.target)) {
            menuList.classList.remove('show');
        }
    });
}

// Check if download is already in progress on page load
document.addEventListener('DOMContentLoaded', function () {
    const galleryId = parseInt(document.getElementById('gallery_id').innerText.replace('#', ''));
    fetch(`/func/progress/${galleryId}`)
        .then(response => response.json())
        .then(data => {
            if (!data.error && (data.status === "pending" || data.status === "downloading")) {
                const addButton = document.getElementById("add");
                addButton.classList.add("btn-disabled");
                showCancelButton(galleryId);

                if (data.status === "pending") {
                    addButton.innerText = "Pending...";
                } else {
                    const percentage = Math.round(data.progress_percentage);
                    addButton.innerText = `Downloading...${percentage}%`;
                }

                startProgressMonitoring(galleryId);
            }
        })
        .catch(error => {
            // No progress found, that's fine
        });
});

