// _currentArticle and _currentArticleUrl hold the Readability result for the currently open
// article. loadContent sets them after extracting; sendToKindle reads them to skip a redundant
// fetch when the user sends to Kindle from the reader view.

/** Fetch, extract and render article content, then cache it server-side for future visits. */
async function loadContent(articleUrl, contentUrl, saveUrl) {
    try {
        const resp = await fetch(articleUrl);
        const html = await resp.text();

        const doc = new DOMParser().parseFromString(html, 'text/html');
        const base = doc.createElement('base');
        base.href = contentUrl;
        doc.head.insertBefore(base, doc.head.firstChild);

        const article = new Readability(doc).parse();
        if (!article) throw new Error('parse failed');

        const tmp = document.createElement('div');
        tmp.innerHTML = article.content;
        for (const attr of ['data-src', 'data-lazy-src', 'data-td-src-property', 'data-srcset']) {
            tmp.querySelectorAll(`img[${attr}]`).forEach(img => {
                const src = img.getAttribute(attr);
                while (img.attributes.length) img.removeAttribute(img.attributes[0].name);
                img.src = src;
            });
        }
        tmp.querySelectorAll('iframe[height]').forEach(el => el.removeAttribute('height'));
        article.content = tmp.innerHTML;

        window._currentArticle = article;
        window._currentArticleUrl = contentUrl;

        document.querySelector('.entry-content').innerHTML = article.content;

        fetch(saveUrl, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({content: article.content})
        });
    } catch(e) {
        document.querySelector('.entry-content').innerHTML =
            `<p><a href="${contentUrl}" target="_blank">Open in browser</a></p>`;
    }
}

/** On long-press of a link in reader content, suppress the native callout and instead swap
 *  the mobile navbar to the autocomplete input pre-filled with the link URL, triggering the
 *  URL action options (discover feed, add as entry, etc.). */
function setupReaderLongPress() {
    const content = document.querySelector('.entry-content');
    if (!content) return;

    content.addEventListener('contextmenu', function (e) {
        const link = e.target.closest('a');
        if (!link) return;
        e.preventDefault();

        const autocomplete = document.querySelector('.navbar-autocomplete');
        const level = document.querySelector('.navbar-brand .level');
        const input = autocomplete.querySelector('.search');

        autocomplete.classList.remove('is-hidden');
        level.classList.add('is-hidden');
        input.value = link.href;
        input.dispatchEvent(new Event('paste'));
        input.focus();
    });
}

document.addEventListener('DOMContentLoaded', setupReaderLongPress);

/** Extract the article (or reuse it if already loaded in reader view) and send it to Kindle. */
async function sendToKindle(entryId, contentUrl) {
    let article;
    if (window._currentArticle && window._currentArticleUrl === contentUrl) {
        article = window._currentArticle;
    } else {
        const resp = await fetch(`/entries/${entryId}/article`);
        const html = await resp.text();
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const base = doc.createElement('base');
        base.href = contentUrl;
        doc.head.insertBefore(base, doc.head.firstChild);
        article = new Readability(doc).parse();
    }
    await fetch('/entries/kindle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            url: contentUrl,
            content: article.content,
            title: article.title,
            byline: article.byline,
            siteName: article.siteName,
            publishedTime: article.publishedTime,
            lang: article.lang
        })
    });
}
