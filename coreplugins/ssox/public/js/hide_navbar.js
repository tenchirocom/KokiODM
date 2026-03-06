function getCookie(name) {
    var match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    if (!match) return [];
    return match[2].split(',').map(function(s) { return s.trim(); });
}

document.addEventListener('DOMContentLoaded', function() {
    if (getCookie('ssox_format').includes('embed')) {
        var header = document.querySelector('.navbar-header');
        if (header) header.style.display = 'none';
        var topLinks = document.querySelector('.navbar-top-links');
        if (topLinks) topLinks.style.display = 'none';
        var navbar = document.getElementById('navbar-top');
        if (navbar) {
            navbar.style.minHeight = '0';
            navbar.style.height = '1px';
        }
        var wrapper = document.getElementById('page-wrapper');
        if (wrapper) wrapper.style.marginTop = '0';
    }
});