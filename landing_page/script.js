document.addEventListener('DOMContentLoaded', () => {
    // Smooth scrolling for anchor links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            const href = this.getAttribute('href');
            // A bare "#" (the brand links) means "back to the top".
            // document.querySelector('#') would throw a SyntaxError.
            if (href === '#') {
                window.scrollTo({ top: 0, behavior: 'smooth' });
                return;
            }
            let target = null;
            try {
                target = document.querySelector(href);
            } catch (err) {
                // Not a valid CSS selector (for example "#1abc"): look the id up directly
                target = document.getElementById(href.slice(1));
            }
            if (target) {
                target.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });

    // Simple intersection observer for entrance animations
    const observerOptions = {
        threshold: 0.1
    };

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.style.opacity = '1';
                entry.target.style.transform = 'translateY(0)';
                observer.unobserve(entry.target);
            }
        });
    }, observerOptions);

    document.querySelectorAll('.card-white, .card-obsidian, h2, .hero-content').forEach(el => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(20px)';
        el.style.transition = 'all 0.6s cubic-bezier(0.175, 0.885, 0.32, 1.275)';
        observer.observe(el);
    });

    // Form submission handling
    const form = document.querySelector('form');
    if (form) {
        form.addEventListener('submit', (e) => {
            e.preventDefault();
            const btn = form.querySelector('button');
            const originalText = btn.textContent;
            
            btn.textContent = 'Отправка...';
            btn.disabled = true;
            
            setTimeout(() => {
                alert('Спасибо! Мы свяжемся с вами в ближайшее время.');
                form.reset();
                btn.textContent = originalText;
                btn.disabled = false;
            }, 1000);
        });
    }
});
