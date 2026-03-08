    // Immediate execution to prevent flash
    (function() {
      const savedTheme = localStorage.getItem('theme');
      const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

      if (savedTheme === 'dark' || (!savedTheme && systemPrefersDark)) {
        document.documentElement.classList.add('dark-mode');
      }
    })();

    function docReady(fn) {
        if (document.readyState === "complete" || document.readyState === "interactive") {
            setTimeout(fn, 1);
        } else {
            document.addEventListener("DOMContentLoaded", fn);
        }
    };

    docReady(function() {
        var t = document.getElementById("toc");
        if (t) {
            t.insertAdjacentHTML("beforeend", "<div id='generated-toc'></div>");
            tocbot.init({
                tocSelector: '#generated-toc',
                contentSelector: '#content',
                headingSelector: document.getElementById("content").getElementsByTagName("h1").length > 0 ? "h1,h2,h3,h4,h5" : "h2,h3,h4,h5",
                hasInnerContainers: false,
                listClass: 'toc-list',
                listItemClass: 'toc-list-item',
                activeListItemClass: 'toc-list-item-focus',
                activeLinkClass: 'toc-is-active-link',
                tocScrollOffset: 50
            });
        }

        const toggleCheckbox = document.getElementById('mode-checkbox');
        if (toggleCheckbox) {
            toggleCheckbox.addEventListener('change', () => {
              var toc = document.getElementById('toc');
              if (toc) { toc.classList.add('trans'); }
              document.body.classList.add('trans');

              if (toggleCheckbox.checked) {
                document.documentElement.classList.add('dark-mode');
                localStorage.setItem('theme', 'dark');
              } else {
                document.documentElement.classList.remove('dark-mode');
                localStorage.setItem('theme', 'light');
              }
            });

            // Sync checkbox to the dark-mode class already applied by the IIFE
            toggleCheckbox.checked = document.documentElement.classList.contains('dark-mode');
        }

        if (typeof Prism !== 'undefined') {
            Prism.highlightAll();
        }
    });
