// main.js — students will add JavaScript here as features are built

document.addEventListener('DOMContentLoaded', function () {
    var openBtn = document.getElementById('how-it-works-btn');
    var modal = document.getElementById('how-it-works-modal');
    var closeBtn = document.getElementById('how-it-works-close');
    var video = document.getElementById('how-it-works-video');

    if (!openBtn || !modal || !closeBtn || !video) return;

    function openModal(event) {
        event.preventDefault();
        var src = video.getAttribute('data-src');
        video.src = src + (src.indexOf('?') > -1 ? '&' : '?') + 'autoplay=1';
        modal.hidden = false;
        document.body.style.overflow = 'hidden';
    }

    function closeModal() {
        modal.hidden = true;
        document.body.style.overflow = '';
        video.src = '';
    }

    openBtn.addEventListener('click', openModal);
    closeBtn.addEventListener('click', closeModal);

    modal.addEventListener('click', function (event) {
        if (event.target === modal) closeModal();
    });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && !modal.hidden) closeModal();
    });
});
