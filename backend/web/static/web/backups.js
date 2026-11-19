/* Komersia – page Sauvegardes (édition locale) : état de la dernière sauvegarde, sauvegarde immédiate, dossier de
   destination, historique, et procédure de restauration (jamais lancée depuis le navigateur). */
(function () {
  'use strict';

  const K = window.Komersia;
  const { h, clear, api, toast, busy, fmtDateTime } = K;
  const B = K.backups;

  /** Message lisible d'une erreur de validation de l'API (champs « folder », « retention »...). */
  function problem(e) {
    const d = e && e.data;
    if (d && typeof d === 'object') {
      const first = (v) => (Array.isArray(v) ? v[0] : v);
      if (d.folder) return String(first(d.folder));
      if (d.retention) return 'Nombre de sauvegardes à conserver : ' + String(first(d.retention));
      if (d.preferred_hour) return 'Heure préférée : ' + String(first(d.preferred_hour));
    }
    return (e && e.message) || 'Une erreur est survenue.';
  }

  K.pages.backups = async function backups(view, ctx) {
    const owner = B.canConfigure(ctx.role);
    const S = { status: null, settings: null, records: [], next: null };
    const el = { status: h('div', { 'aria-live': 'polite' }), folder: h('div'), history: h('div'), restore: h('div') };
    let running = false;

    async function load() {
      const [status, settings, records] = await Promise.all([
        api('backups/status/'), api('backups/settings/'), api('backups/records/')]);
      S.status = status; S.settings = settings; S.records = records.results || []; S.next = records.next;
    }

    /* ---------- état ---------- */
    function renderStatus() {
      const st = S.status;
      const sum = B.summary(st);
      const klass = { ok: 'alert-ok', warn: 'alert-warn', danger: 'alert-error' }[sum.tone];
      const box = h('section', { class: 'alert ' + klass, 'aria-labelledby': 'bkp-title', role: sum.tone === 'danger' ? 'alert' : 'status' },
        h('h2', { id: 'bkp-title' }, sum.title));
      if (sum.text) box.appendChild(h('p', null, sum.text));
      const folderOk = st.folder_state === 'ok';
      const facts = h('dl', { class: 'detail-grid' });
      const fact = (k, v) => { facts.append(h('dt', null, k), h('dd', null, v)); };
      fact('Dossier', st.folder ? st.folder : 'Aucun dossier choisi');
      fact('État du dossier', B.folderLabel(st.folder_state));
      if (folderOk && st.free_bytes !== null) fact('Espace libre', B.size(st.free_bytes));
      if (folderOk && st.backup_count !== null) fact('Sauvegardes conservées', String(st.backup_count));
      if (!st.enabled) fact('Sauvegarde automatique', 'Désactivée');
      else if (st.next_run_at) {
        const late = Date.parse(st.next_run_at) <= Date.parse(st.server_time);
        fact('Prochaine sauvegarde', late ? 'dès que possible (le dossier doit être disponible)' : B.when(st.next_run_at, st.server_time));
      }
      const card = h('section', { class: 'card' }, box, facts);
      if (!folderOk) {
        card.appendChild(K.alertBox(st.folder_message || 'Le dossier de sauvegarde n’est pas disponible.', 'warn'));
      }
      const btn = h('button', { type: 'button', class: 'btn', id: 'bkp-run' }, 'Sauvegarder maintenant');
      btn.addEventListener('click', () => run(btn));
      card.appendChild(h('div', { class: 'btn-row' }, btn));
      clear(el.status).appendChild(card);
    }

    async function run(btn) {
      if (running) return;
      running = true;
      const label = btn.textContent;
      btn.textContent = 'Sauvegarde en cours…';
      try {
        await busy(btn, async () => {
          const r = await api('backups/run/', { method: 'POST' });
          if (r.ok) toast('Sauvegarde terminée.', 'ok');
          else toast(r.message || 'La sauvegarde a échoué.', 'error');
        });
      } catch (e) {
        toast(e.status === 409 ? 'Une sauvegarde est déjà en cours.' : problem(e), 'error');
      } finally {
        running = false;
        btn.textContent = label;
      }
      await refresh();
    }

    /* ---------- dossier de destination ---------- */
    function renderFolder() {
      const s = S.settings;
      const card = h('section', { class: 'card' }, h('h2', null, 'Où sauvegarder'));
      const folder = K.input({ type: 'text', value: s.folder, autocomplete: 'off', spellcheck: 'false', disabled: !owner, placeholder: 'D:\\Sauvegardes' });
      const keep = K.input({ type: 'number', min: '1', max: '365', inputmode: 'numeric', value: String(s.retention), disabled: !owner });
      const auto = K.input({ type: 'checkbox', checked: !!s.enabled, disabled: !owner });
      const error = h('div', { 'aria-live': 'polite' });
      card.append(
        K.field('Dossier de destination', folder,
          'Sur le PC principal, par exemple D:\\Sauvegardes ou la lettre de votre clé USB (E:\\Komersia). Le dossier doit exister. Choisissez un disque ou une clé différents de ceux où Komersia est installé.'),
        K.field('Nombre de sauvegardes à conserver', keep, 'Les plus anciennes sont supprimées automatiquement ; la plus récente est toujours gardée.'),
        h('div', { class: 'field check' }, auto, h('label', { for: (auto.id = auto.id || 'bkp-auto') }, 'Sauvegarde automatique chaque jour')),
        error);
      if (owner) {
        const save = h('button', { type: 'button', class: 'btn', id: 'bkp-save' }, 'Enregistrer');
        save.addEventListener('click', async () => {
          clear(error);
          const n = parseInt(keep.value, 10);
          if (!folder.value.trim()) { error.appendChild(K.alertBox('Indiquez le dossier de sauvegarde.')); return; }
          if (!(n >= 1 && n <= 365)) { error.appendChild(K.alertBox('Le nombre de sauvegardes à conserver doit être entre 1 et 365.')); return; }
          try {
            await busy(save, async () => {
              S.settings = await api('backups/settings/', { method: 'PUT', body: { folder: folder.value.trim(), retention: n, enabled: auto.checked } });
              toast('Réglages enregistrés.', 'ok');
            });
            await refresh();
          } catch (e) {
            error.appendChild(K.alertBox(problem(e)));
          }
        });
        card.appendChild(h('div', { class: 'btn-row' }, save));
      } else {
        card.appendChild(h('p', { class: 'hint' }, 'Seul le propriétaire peut modifier ces réglages.'));
      }
      clear(el.folder).appendChild(card);
    }

    /* ---------- historique ---------- */
    function row(r) {
      const tone = B.statusTone(r.status);
      const sub = [B.reasonLabel(r.reason)];
      if (r.status === 'ok') sub.push(B.size(r.size_bytes), r.filename);
      else sub.push(r.message);
      if (r.attempts > 1) sub.push(r.attempts + ' tentatives');
      if (r.created_by_name) sub.push(r.created_by_name);
      return h('div', { class: 'row-card' },
        h('div', { class: 'main' }, h('div', { class: 'title' }, fmtDateTime(r.created_at)), h('div', { class: 'sub' }, sub.join(' · '))),
        h('span', { class: 'badge badge-' + tone }, B.statusLabel(r.status)));
    }

    function renderHistory() {
      const card = h('section', { class: 'card' }, h('h2', null, 'Historique'));
      if (!S.records.length) card.appendChild(h('p', { class: 'empty' }, 'Aucune sauvegarde pour le moment.'));
      else {
        const list = h('div', { class: 'list' });
        S.records.forEach((r) => list.appendChild(row(r)));
        card.appendChild(list);
      }
      if (S.next) {
        const more = h('button', { type: 'button', class: 'btn btn-ghost more-btn' }, 'Afficher plus');
        more.addEventListener('click', async () => {
          try {
            await busy(more, async () => {
              const page = await api('backups/records/?page=' + (Math.floor(S.records.length / 50) + 1));
              S.records = S.records.concat(page.results || []);
              S.next = page.next;
            });
            renderHistory();
          } catch (e) { toast(problem(e), 'error'); }
        });
        card.appendChild(more);
      }
      clear(el.history).appendChild(card);
    }

    /* ---------- restauration : procédure seulement ---------- */
    function renderRestore() {
      el.restore.appendChild(h('section', { class: 'card' },
        h('h2', null, 'Restaurer une sauvegarde'),
        K.alertBox('La restauration remplace toutes les données actuelles par celles de la sauvegarde. Elle ne se lance pas depuis cette page.', 'warn'),
        h('ol', null,
          h('li', null, 'Fermez Komersia et branchez le disque ou la clé USB qui contient les sauvegardes.'),
          h('li', null, 'Ouvrez le menu Démarrer, puis Komersia, puis « Restaurer une sauvegarde ».'),
          h('li', null, 'Choisissez le fichier voulu (le nom indique la date : komersia-AAAAMMJJ-HHMMSS.dump).'),
          h('li', null, 'Komersia vérifie le fichier, garde d’abord une copie de sécurité de vos données actuelles, puis restaure.'),
          h('li', null, 'Rouvrez Komersia et contrôlez vos dernières ventes.')),
        h('p', { class: 'hint' }, 'Pour un technicien : manage.py restore_backup <fichier> --yes (voir la documentation « Sauvegardes locales »). Conseil : testez une restauration une fois par mois, sur un autre PC si possible.')));
    }

    async function refresh() {
      try {
        await load();
      } catch (e) {
        toast(e.message, 'error');
        return;
      }
      renderStatus(); renderFolder(); renderHistory();
      K.refreshBackups();
    }

    try {
      await load();
    } catch (e) {
      clear(view).appendChild(K.alertBox(e.status === 404
        ? 'La sauvegarde automatique est disponible dans l’édition installée sur le PC principal du commerce.'
        : e.message, e.status === 404 ? 'info' : 'error'));
      return;
    }
    renderRestore();
    clear(view).append(el.status, el.folder, el.history, el.restore);
    renderStatus(); renderFolder(); renderHistory();
  };
}());
