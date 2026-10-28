// Markdown leve e seguro (sem CDN): escapa HTML primeiro, depois aplica a formatação.
(function () {
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const PH = (i) => '\u0000' + i + '\u0000';

  function inline(s, guarda) {
    // código inline
    s = s.replace(/`([^`\n]+)`/g, (m, c) => { guarda.push('<code>' + c + '</code>'); return PH(guarda.length - 1); });
    // links markdown [texto](http...)
    s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (m, t, u) => {
      guarda.push('<a href="' + u + '" target="_blank" rel="noopener noreferrer">' + t + '</a>'); return PH(guarda.length - 1);
    });
    // links soltos
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?])/g, (m, pre, u) => {
      guarda.push('<a href="' + u + '" target="_blank" rel="noopener noreferrer">' + u + '</a>'); return pre + PH(guarda.length - 1);
    });
    s = s.replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>')
         .replace(/__([^_\n]+?)__/g, '<strong>$1</strong>')
         .replace(/(^|[^\w*])\*([^*\n]+?)\*(?!\w)/g, '$1<em>$2</em>')
         .replace(/(^|[^\w])_([^_\n]+?)_(?!\w)/g, '$1<em>$2</em>')
         .replace(/~~([^~\n]+?)~~/g, '<del>$1</del>');
    return s;
  }

  function md(src) {
    const guarda = [];
    let s = String(src == null ? '' : src).replace(/\r\n?/g, '\n');
    // blocos de código (inclusive um ainda aberto durante o streaming)
    s = s.replace(/```[\w+-]*\n?([\s\S]*?)(```|$)/g, (m, code) => {
      guarda.push('<pre><code>' + esc(code.replace(/\n$/, '')) + '</code></pre>'); return '\n' + PH(guarda.length - 1) + '\n';
    });
    const linhas = esc(s).split('\n');
    const out = [];
    let i = 0;
    const ehTabela = l => /^\s*\|.*\|\s*$/.test(l);
    while (i < linhas.length) {
      let l = linhas[i];
      if (!l.trim()) { i++; continue; }
      if (/^\u0000\d+\u0000$/.test(l.trim())) { out.push(l.trim()); i++; continue; }
      let m;
      if ((m = l.match(/^(#{1,4})\s+(.*)$/))) { const n = Math.min(m[1].length + 2, 6); out.push(`<h${n}>${inline(m[2], guarda)}</h${n}>`); i++; continue; }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(l)) { out.push('<hr>'); i++; continue; }
      if (/^&gt;\s?/.test(l)) {
        const q = [];
        while (i < linhas.length && /^&gt;\s?/.test(linhas[i])) { q.push(linhas[i].replace(/^&gt;\s?/, '')); i++; }
        out.push('<blockquote>' + q.map(x => inline(x, guarda)).join('<br>') + '</blockquote>'); continue;
      }
      if (ehTabela(l) && i + 1 < linhas.length && /^\s*\|?\s*:?-{2,}/.test(linhas[i + 1])) {
        const cel = x => x.trim().replace(/^\||\|$/g, '').split('|').map(c => inline(c.trim(), guarda));
        const cab = cel(l); i += 2;
        let h = '<div class="tab"><table><thead><tr>' + cab.map(c => `<th>${c}</th>`).join('') + '</tr></thead><tbody>';
        while (i < linhas.length && ehTabela(linhas[i])) { h += '<tr>' + cel(linhas[i]).map(c => `<td>${c}</td>`).join('') + '</tr>'; i++; }
        out.push(h + '</tbody></table></div>'); continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(l)) {
        const ordenada = /^\s*\d+[.)]/.test(l);
        const itens = [];
        while (i < linhas.length && (/^\s*([-*+]|\d+[.)])\s+/.test(linhas[i]) || (/^\s{2,}\S/.test(linhas[i]) && itens.length))) {
          if (/^\s*([-*+]|\d+[.)])\s+/.test(linhas[i])) {
            const sub = /^\s{2,}/.test(linhas[i]);
            itens.push({ t: linhas[i].replace(/^\s*([-*+]|\d+[.)])\s+/, ''), sub });
          } else itens[itens.length - 1].t += ' ' + linhas[i].trim();
          i++;
        }
        const tag = ordenada ? 'ol' : 'ul';
        out.push(`<${tag}>` + itens.map(x => `<li${x.sub ? ' class="sub"' : ''}>${inline(x.t, guarda)}</li>`).join('') + `</${tag}>`);
        continue;
      }
      const p = [];
      while (i < linhas.length && linhas[i].trim() && !/^(#{1,4}\s|&gt;|\s*([-*+]|\d+[.)])\s+|\u0000\d+\u0000$)/.test(linhas[i]) && !(ehTabela(linhas[i]) && p.length === 0 && false)) {
        p.push(inline(linhas[i], guarda)); i++;
        if (i < linhas.length && ehTabela(linhas[i]) && i + 1 < linhas.length && /^\s*\|?\s*:?-{2,}/.test(linhas[i + 1])) break;
      }
      if (p.length) out.push('<p>' + p.join('<br>') + '</p>');
      else { out.push('<p>' + inline(l, guarda) + '</p>'); i++; }
    }
    let html = out.join('');
    for (let k = 0; k < 3; k++) html = html.replace(/\u0000(\d+)\u0000/g, (m, n) => guarda[+n]);
    return html;
  }
  window.md = md;
  window.esc = esc;
})();
