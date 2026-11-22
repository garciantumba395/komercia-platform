/* Komersia – noyau de l'interface web : API, formats, composants, menu, connexion et inscription.
   JavaScript natif, aucune dépendance externe. Les pages sont dans pages.js et pos.js. */
(function () {
  'use strict';

  const K = (window.Komersia = { pages: {}, ctx: {} });
  const API = '/api/v1/';
  const NBSP = ' ';

  /* ---------- Stockage local (jamais bloquant) ---------- */
  const store = {
    get(key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } },
    set(key, value) { try { window.localStorage.setItem(key, value); } catch (e) { /* ignoré */ } },
    del(key) { try { window.localStorage.removeItem(key); } catch (e) { /* ignoré */ } },
  };
  K.store = store;

  /* ---------- Rôles et menu (matrice : cahier des charges, section 19) ---------- */
  const ROLE_LABELS = {
    owner: 'Propriétaire', manager: 'Gérant', cashier: 'Caissier', storekeeper: 'Magasinier', accountant: 'Comptable',
  };
  const NAV = [
    { id: 'pos', href: '/app/caisse/', label: 'Caisse', icon: '🛒', roles: ['owner', 'manager', 'cashier'] },
    { id: 'products', href: '/app/produits/', label: 'Produits', icon: '📦', roles: ['owner', 'manager', 'storekeeper'] },
    { id: 'stock', href: '/app/stock/', label: 'Stock', icon: '🏷️', roles: ['owner', 'manager', 'storekeeper'] },
    { id: 'customers', href: '/app/clients/', label: 'Clients et dettes', icon: '👥', roles: ['owner', 'manager', 'cashier', 'accountant'] },
    { id: 'sales', href: '/app/ventes/', label: 'Ventes du jour', icon: '🧾', roles: ['owner', 'manager', 'cashier', 'accountant'] },
    { id: 'dashboard', href: '/app/tableau-de-bord/', label: 'Tableau de bord', icon: '📊', roles: ['owner', 'manager', 'accountant'] },
    { id: 'rates', href: '/app/taux/', label: 'Taux de change', icon: '💱', roles: ['owner', 'manager', 'accountant'] },
    { id: 'users', href: '/app/utilisateurs/', label: 'Utilisateurs', icon: '🔑', roles: ['owner'] },
    { id: 'billing', href: '/app/abonnement/', label: 'Abonnement', icon: '💳', roles: ['owner', 'manager', 'accountant'] },
    { id: 'backups', href: '/app/sauvegardes/', label: 'Sauvegardes', icon: '💾', roles: ['owner', 'manager'], optional: true },
  ];
  const ICONS = {"pos": "<path d=\"M3 4h2l2.2 10.2a1.5 1.5 0 0 0 1.5 1.2h8.2a1.5 1.5 0 0 0 1.5-1.1L20 8H6.2\"/><circle cx=\"9\" cy=\"19.5\" r=\"1.4\"/><circle cx=\"17\" cy=\"19.5\" r=\"1.4\"/>", "products": "<path d=\"M21 8l-9-5-9 5 9 5 9-5z\"/><path d=\"M3 8v8l9 5 9-5V8\"/><path d=\"M12 13v8\"/>", "stock": "<path d=\"M3 21V9l9-6 9 6v12\"/><path d=\"M8 21v-7h8v7\"/>", "customers": "<circle cx=\"9\" cy=\"8\" r=\"3.2\"/><path d=\"M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6\"/><path d=\"M16 5.3a3.2 3.2 0 0 1 0 5.9M18 14.4c1.9.8 3 2.7 3 5.6\"/>", "sales": "<path d=\"M6 3h12v18l-3-2-3 2-3-2-3 2V3z\"/><path d=\"M9 8h6M9 12h6\"/>", "dashboard": "<path d=\"M4 20V10M10 20V4M16 20v-7M22 20H2\"/>", "rates": "<path d=\"M4 8h14l-3-3M20 16H6l3 3\"/>", "users": "<circle cx=\"8\" cy=\"15\" r=\"4\"/><path d=\"M11 12l8-8M16 7l2 2M14 9l2 2\"/>", "billing": "<rect x=\"3\" y=\"5\" width=\"18\" height=\"14\" rx=\"2.5\"/><path d=\"M3 10h18M7 15h4\"/>", "backups": "<ellipse cx=\"12\" cy=\"6\" rx=\"8\" ry=\"3\"/><path d=\"M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6\"/>"};
  const svgIcon = (id) => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICONS[id] || '') + '</svg>';
  K.svgIcon = svgIcon;
  K.ROLE_LABELS = ROLE_LABELS;
  const canOpen = (id, role) => NAV.some((n) => n.id === id && n.roles.includes(role));
  const homeFor = (role) => (NAV.find((n) => n.roles.includes(role)) || NAV[0]).href;
  K.canWrite = {
    products: (role) => ['owner', 'manager'].includes(role),
    stock: (role) => ['owner', 'manager', 'storekeeper'].includes(role),
    customers: (role) => ['owner', 'manager', 'cashier'].includes(role),
    limit: (role) => ['owner', 'manager'].includes(role),
    rates: (role) => ['owner', 'manager'].includes(role),
    cancel: (role) => ['owner', 'manager'].includes(role),
  };

  /* ---------- Montants : entiers en plus petite unité (CDF : franc ; USD : centime) ---------- */
  const group = (digits, sep) => String(digits).replace(/\B(?=(\d{3})+(?!\d))/g, sep);

  function fmt(minor, currency) {
    const n = Math.round(Number(minor) || 0);
    const neg = n < 0 ? '-' : '';
    const abs = Math.abs(n);
    if (currency === 'USD') {
      return neg + '$' + group(Math.floor(abs / 100), ',') + '.' + String(abs % 100).padStart(2, '0');
    }
    return neg + group(abs, NBSP) + NBSP + 'FC';
  }

  /** « 12 500 » ou « 12,50 » → entier en plus petite unité ; '' → 0 ; invalide → null. */
  function parseAmount(text, currency) {
    const s = String(text == null ? '' : text).trim().replace(/[\s  ]/g, '').replace(',', '.');
    if (s === '') return 0;
    if (!/^\d+(\.\d+)?$/.test(s)) return null;
    const [int, frac = ''] = s.split('.');
    if (currency === 'USD') {
      if (frac.length > 2) return null;
      return parseInt(int, 10) * 100 + parseInt((frac + '00').slice(0, 2), 10);
    }
    if (/[1-9]/.test(frac)) return null; // le franc n'a pas de décimales
    return parseInt(int, 10);
  }

  /** Entier → texte à mettre dans un champ de saisie. */
  function toInput(minor, currency) {
    const n = Number(minor) || 0;
    if (currency === 'USD') return (n / 100).toFixed(2);
    return String(n);
  }

  // Taux CDF pour 1 USD, en entier mis à l'échelle 10^4 (BigInt : aucun flottant).
  function rateScaled(rate) {
    const [i, f = ''] = String(rate).split('.');
    return BigInt(i || '0') * 10000n + BigInt((f + '0000').slice(0, 4));
  }
  const divRound = (n, d) => (n * 2n + d) / (2n * d); // arrondi « moitié vers le haut », n >= 0

  /** Convertit un montant (plus petite unité) d'une devise à l'autre. rs = rateScaled(taux). */
  function convert(minor, from, to, rs) {
    if (from === to) return minor;
    const m = BigInt(minor);
    if (from === 'CDF') return Number(divRound(m * 1000000n, rs)); // CDF -> centimes
    return Number(divRound(m * rs, 1000000n)); // centimes -> CDF
  }

  /** Paiement mixte : somme exacte arrondie UNE fois dans la devise de la vente (comme le serveur). */
  function paidInCurrency(cdf, usd, saleCurrency, rs) {
    const num = BigInt(cdf) * 1000000n + BigInt(usd) * rs;
    return Number(divRound(num, saleCurrency === 'CDF' ? 1000000n : rs));
  }

  function fmtQty(value) {
    const n = parseFloat(value);
    if (Number.isNaN(n)) return String(value);
    return n.toLocaleString('fr-FR', { maximumFractionDigits: 3 }).replace(/ /g, NBSP);
  }

  const dtFormat = new Intl.DateTimeFormat('fr-FR', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Kinshasa',
  });
  const dFormat = new Intl.DateTimeFormat('fr-FR', {
    day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'Africa/Kinshasa',
  });
  const dayKey = new Intl.DateTimeFormat('en-CA', { timeZone: 'Africa/Kinshasa' });
  function fmtDateTime(iso) { try { return dtFormat.format(new Date(iso)); } catch (e) { return ''; } }
  function fmtDate(iso) { try { return dFormat.format(new Date(iso)); } catch (e) { return ''; } }
  const todayISO = () => dayKey.format(new Date()); // AAAA-MM-JJ à Kinshasa
  const dayOf = (iso) => { try { return dayKey.format(new Date(iso)); } catch (e) { return ''; } };

  /** Montants par devise ({"CDF": 1000, "USD": 250}) → « 1 000 FC · $2.50 ». */
  function fmtMap(map) {
    const parts = [];
    ['CDF', 'USD'].forEach((c) => { if (map && map[c]) parts.push(fmt(map[c], c)); });
    return parts.length ? parts.join(' · ') : fmt(0, 'CDF');
  }

  Object.assign(K, { fmt, parseAmount, toInput, rateScaled, convert, paidInCurrency, fmtQty, fmtDateTime, fmtDate, todayISO, dayOf, fmtMap });

  /* ---------- Abonnement : logique pure (bandeau, libellés) ---------- */
  const BILLING_READERS = ['owner', 'manager', 'accountant']; // l'API de facturation est interdite aux autres rôles
  const PAYMENT_STATUS = {
    pending: ['En attente', 'warn'], confirmed: ['Confirmé', 'ok'], failed: ['Échoué', 'danger'], expired: ['Expiré', 'muted'],
  };
  const PAYMENT_KINDS = { subscribe: 'Abonnement', upgrade: 'Complément de plan' };
  const STATE_LABELS = { trial: 'Essai gratuit', active: 'Actif', past_due: 'Paiement en retard', read_only: 'Lecture seule' };
  const NOTICE_TEXTS = {
    trial_7: 'Votre essai gratuit se termine dans 7 jours.',
    trial_3: 'Votre essai gratuit se termine dans 3 jours.',
    trial_1: 'Votre essai gratuit se termine demain.',
    trial_0: 'Votre essai gratuit se termine aujourd’hui.',
    due_7: 'Votre abonnement arrive à échéance dans 7 jours.',
    due_3: 'Votre abonnement arrive à échéance dans 3 jours.',
    due_0: 'Votre abonnement arrive à échéance aujourd’hui.',
    past_due: 'Paiement en retard : payez pour éviter le passage en lecture seule.',
    read_only: 'Votre abonnement est terminé : le compte est en lecture seule.',
  };
  const INACTIVE_MESSAGE = 'Abonnement terminé. Demandez au propriétaire de s’abonner.';

  /** « 5 jours restants », « 1 jour restant », « Dernier jour ». */
  function daysLabel(n) {
    const d = Number(n);
    if (!Number.isFinite(d) || d <= 0) return 'Dernier jour';
    return d === 1 ? '1 jour restant' : d + ' jours restants';
  }

  /** Bandeau global selon l'état de l'abonnement : { tone: info|warn|danger, text, action } ou null. */
  function bannerFor(sub, role) {
    if (!sub) return null;
    const owner = role === 'owner';
    const d = Number(sub.days_left);
    const action = (label) => ({ label: owner ? label : 'Voir l’abonnement', href: '/app/abonnement/' });
    if (sub.state === 'trial') {
      const text = d <= 0 ? 'Dernier jour d’essai gratuit.' : d === 1 ? 'Il vous reste 1 jour d’essai.' : 'Il vous reste ' + d + ' jours d’essai.';
      return { tone: d <= 3 ? 'warn' : 'info', text, action: action('S’abonner') };
    }
    if (sub.state === 'past_due') {
      const grace = d <= 0 ? 'dernier jour de grâce' : d === 1 ? '1 jour de grâce' : d + ' jours de grâce';
      return { tone: 'warn', text: 'Paiement attendu : ' + grace + ' avant le passage en lecture seule.', action: action('Payer') };
    }
    if (sub.state === 'read_only') {
      return {
        tone: 'danger',
        text: 'Abonnement terminé : consultation et export seulement.' + (owner ? '' : ' Demandez au propriétaire de s’abonner.'),
        action: action('S’abonner'),
      };
    }
    if (sub.state === 'active' && sub.cancel_at_period_end) {
      return { tone: 'info', text: 'Abonnement résilié : il reste actif jusqu’au ' + fmtDate(sub.current_period_end) + '.', action: action('Reprendre') };
    }
    return null;
  }
  const paymentStatus = (s) => (PAYMENT_STATUS[s] || [String(s || ''), 'muted']);
  const paymentStatusLabel = (s) => paymentStatus(s)[0];
  const isReadOnly = () => !!((K.ctx.sub && K.ctx.sub.state === 'read_only') || K.ctx.inactive);
  K.billing = {
    READERS: BILLING_READERS, PAYMENT_KINDS, STATE_LABELS, NOTICE_TEXTS, INACTIVE_MESSAGE,
    daysLabel, bannerFor, paymentStatus, paymentStatusLabel, isReadOnly,
    canRead: (role) => BILLING_READERS.includes(role),
  };

  /* ---------- Sauvegardes locales : logique pure (libellés, bandeau, alerte) ---------- */
  const hmFormat = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Kinshasa' });
  const BACKUP_STATUS = { ok: ['Réussie', 'ok'], echec: ['Échec', 'danger'], dossier_introuvable: ['Dossier introuvable', 'warn'] };
  const BACKUP_REASON = { scheduled: 'Automatique', manual: 'Manuelle', pre_restore: 'Avant restauration' };
  const FOLDER_STATE = {
    ok: 'Dossier disponible', unset: 'Aucun dossier choisi', missing: 'Dossier introuvable (clé USB débranchée ?)',
    not_writable: 'Dossier en lecture seule ou plein',
  };
  const dayDiff = (iso, now) => {
    const a = dayOf(now || new Date().toISOString()); const b = dayOf(iso);
    return Math.round((Date.parse(a + 'T00:00:00Z') - Date.parse(b + 'T00:00:00Z')) / 86400000);
  };
  /** « aujourd’hui à 02:00 », « hier à 02:00 », « demain à 02:00 », « le 17/11/2026 à 02:00 ». */
  function backupWhen(iso, now) {
    if (!iso || Number.isNaN(Date.parse(iso))) return '';
    const hm = hmFormat.format(new Date(iso));
    const diff = dayDiff(iso, now);
    if (diff === 0) return 'aujourd’hui à ' + hm;
    if (diff === 1) return 'hier à ' + hm;
    if (diff === -1) return 'demain à ' + hm;
    return 'le ' + fmtDate(iso) + ' à ' + hm;
  }
  function backupAge(hours) {
    if (hours === null || hours === undefined) return '';
    if (hours < 1) return 'moins d’une heure';
    if (hours < 48) return Math.floor(hours) + (Math.floor(hours) === 1 ? ' heure' : ' heures');
    return Math.floor(hours / 24) + ' jours';
  }
  function backupSize(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024) return n + ' o';
    const units = ['Ko', 'Mo', 'Go', 'To'];
    let v = n / 1024; let i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
    return v.toLocaleString('fr-FR', { maximumFractionDigits: v < 10 ? 1 : 0 }) + NBSP + units[i];
  }
  /** Grand état de la page : { tone: ok|warn|danger, title, text }. La santé (health) vient du serveur. */
  function backupSummary(st, now) {
    if (!st) return null;
    const tone = { ok: 'ok', late: 'warn', alert: 'danger' }[st.health] || 'warn';
    const attempt = st.last_attempt;
    const failedLast = !!attempt && attempt.status === 'echec' && (!st.last_success || Date.parse(attempt.created_at) > Date.parse(st.last_success.created_at));
    if (!st.last_success) {
      return { tone, title: 'Aucune sauvegarde pour le moment',
        text: failedLast ? 'La dernière tentative a échoué : ' + attempt.message : 'Vos données ne sont pas encore protégées. Cliquez sur « Sauvegarder maintenant ».' };
    }
    const suffix = failedLast ? 'dernière tentative en échec' : tone === 'ok' ? 'OK' : tone === 'warn' ? 'un peu ancienne' : 'trop ancienne (' + backupAge(st.age_hours) + ')';
    return { tone, title: 'Dernière sauvegarde : ' + backupWhen(st.last_success.created_at, now) + ' — ' + suffix,
      text: failedLast ? 'La dernière tentative a échoué : ' + attempt.message : '' };
  }
  /** Bandeau global (toutes les pages) : seulement si la dernière sauvegarde réussie a plus de 2 jours, ou n'existe pas. */
  function backupBannerFor(st) {
    if (!st || !st.banner) return null;
    const text = st.last_success
      ? 'Vos données ne sont plus sauvegardées : la dernière sauvegarde date de ' + backupAge(st.age_hours) + '.'
      : 'Vos données ne sont pas encore sauvegardées.';
    return { tone: 'warn', text, action: { label: 'Voir les sauvegardes', href: '/app/sauvegardes/' } };
  }
  K.backups = {
    ROLES: ['owner', 'manager'], canUse: (role) => ['owner', 'manager'].includes(role), canConfigure: (role) => role === 'owner',
    when: backupWhen, age: backupAge, size: backupSize, summary: backupSummary, bannerFor: backupBannerFor,
    statusLabel: (s) => (BACKUP_STATUS[s] || [String(s || ''), 'muted'])[0],
    statusTone: (s) => (BACKUP_STATUS[s] || [String(s || ''), 'muted'])[1],
    reasonLabel: (r) => BACKUP_REASON[r] || String(r || ''), folderLabel: (s) => FOLDER_STATE[s] || String(s || ''),
  };

  /* ---------- DOM ---------- */
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    Object.entries(props || {}).forEach(([k, v]) => {
      if (v === null || v === undefined || v === false) return;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'hidden' || k === 'selected') el[k] = v;
      else el.setAttribute(k, v === true ? '' : v);
    });
    const add = (kid) => {
      if (kid === null || kid === undefined || kid === false) return;
      if (Array.isArray(kid)) kid.forEach(add);
      else el.appendChild(kid.nodeType ? kid : document.createTextNode(String(kid)));
    };
    kids.forEach(add);
    return el;
  }
  K.h = h;
  const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
  K.clear = clear;

  let uid = 0;
  /** Champ avec étiquette associée (accessibilité). */
  function field(label, control, hint) {
    const id = control.id || 'f' + (++uid);
    control.id = id;
    return h('div', { class: 'field' }, h('label', { for: id }, label), control, hint ? h('div', { class: 'hint' }, hint) : null);
  }
  K.field = field;
  K.input = (props) => h('input', props);
  K.select = (options, value, props) => {
    const sel = h('select', props || {});
    options.forEach(([v, l]) => sel.appendChild(h('option', { value: v, selected: String(v) === String(value) }, l)));
    return sel;
  };

  function toast(message, kind) {
    const root = document.getElementById('toasts');
    if (!root) return;
    const t = h('div', { class: 'toast ' + (kind || '') }, message);
    root.appendChild(t);
    setTimeout(() => t.remove(), kind === 'error' ? 6000 : 3500);
  }
  K.toast = toast;

  function alertBox(message, kind) {
    return h('div', { class: 'alert alert-' + (kind || 'error'), role: kind === 'error' || !kind ? 'alert' : 'status' }, message);
  }
  K.alertBox = alertBox;

  /** Fenêtre modale accessible (Échap, clic à l'extérieur, retour du focus). */
  function openModal(title, build, onClose) {
    const root = document.getElementById('modal-root');
    const previous = document.activeElement;
    const body = h('div');
    const closeBtn = h('button', { type: 'button', class: 'btn btn-ghost btn-small', 'aria-label': 'Fermer' }, '✕ Fermer');
    const modal = h('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      h('div', { class: 'modal-head' }, h('h2', null, title), closeBtn), body);
    const overlay = h('div', { class: 'overlay' }, modal);
    function close() {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      if (previous && previous.focus) previous.focus();
      if (onClose) onClose();
    }
    function onKey(e) { if (e.key === 'Escape') close(); }
    closeBtn.addEventListener('click', close);
    overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) close(); });
    document.addEventListener('keydown', onKey);
    root.appendChild(overlay);
    build(body, close);
    const first = body.querySelector('input:not([type=hidden]), select, textarea, button');
    if (first) first.focus();
    return { close, body };
  }
  K.openModal = openModal;

  function confirmDialog(message, okLabel, danger) {
    return new Promise((resolve) => {
      let answer = false;
      openModal('Confirmation', (body, close) => {
        body.appendChild(h('p', null, message));
        body.appendChild(h('div', { class: 'btn-row' },
          h('button', { type: 'button', class: 'btn ' + (danger ? 'btn-danger' : ''), onclick: () => { answer = true; close(); } }, okLabel || 'Confirmer'),
          h('button', { type: 'button', class: 'btn btn-ghost', onclick: () => close() }, 'Annuler')));
      }, () => resolve(answer));
    });
  }
  K.confirm = confirmDialog;

  /** Désactive un bouton pendant un appel asynchrone. */
  async function busy(button, fn) {
    if (button) button.disabled = true;
    try { return await fn(); } finally { if (button) button.disabled = false; }
  }
  K.busy = busy;

  function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }
  K.debounce = debounce;

  /* ---------- API ---------- */
  class ApiError extends Error {
    constructor(status, data, message) { super(message); this.status = status; this.data = data; }
  }
  K.ApiError = ApiError;

  const FIELD_LABELS = {
    full_name: 'Nom complet', phone: 'Téléphone', email: 'E-mail', password: 'Mot de passe',
    business_name: 'Nom de l’entreprise', sector: 'Secteur', base_currency: 'Devise', address: 'Adresse',
    name: 'Nom', sku: 'SKU', barcode: 'Code-barres', category: 'Catégorie', unit: 'Unité', price_minor: 'Prix',
    cost_minor: 'Coût d’achat', min_stock: 'Seuil minimum', quantity: 'Quantité', reason: 'Motif',
    reference: 'Référence', unit_cost_minor: 'Coût unitaire', rate_cdf_per_usd: 'Taux', amount_minor: 'Montant',
    currency: 'Devise', role: 'Rôle', file: 'Fichier', store: 'Boutique', product: 'Produit', customer: 'Client',
    items: 'Articles', payments: 'Paiements', discount_minor: 'Remise', credit_limit_minor: 'Limite de crédit',
    opening_float: 'Fonds initial', counted: 'Montant compté', client_id: 'Identifiant de vente',
  };

  function flatten(data, prefix) {
    if (data === null || data === undefined) return [];
    if (typeof data === 'string') return [prefix ? prefix + ' : ' + data : data];
    if (Array.isArray(data)) return data.flatMap((d) => flatten(d, prefix));
    if (typeof data === 'object') {
      return Object.entries(data).flatMap(([k, v]) => {
        if (k === 'detail' || k === 'non_field_errors' || k === 'error' || k === 'message') return flatten(v, prefix);
        return flatten(v, FIELD_LABELS[k] || k);
      });
    }
    return [String(data)];
  }

  function describe(status, data, isJson) {
    if (status === 401) return 'Votre session a expiré ou votre compte n’est plus actif. Reconnectez-vous.';
    if (status === 429) return 'Trop de tentatives : patientez une minute puis réessayez.';
    if (status >= 500) return 'Erreur du serveur. Réessayez dans un instant.';
    if (!isJson && status === 404) return 'Service indisponible pour le moment.';
    if (status === 403 && !flatten(data).length) return 'Vous n’avez pas le droit d’effectuer cette action.';
    if (status === 404 && !flatten(data).length) return 'Élément introuvable.';
    const msgs = flatten(data);
    return msgs.length ? msgs.join(' ') : 'Une erreur est survenue (code ' + status + ').';
  }

  function logout(redirect) {
    ['kom_token', 'kom_role', 'kom_name', 'kom_tenant', 'kom_welcome'].forEach(store.del);
    if (redirect === 'expired') window.location.replace('/app/connexion/?expired=1');
    else if (redirect !== false) window.location.replace('/app/connexion/');
  }
  K.logout = logout;

  /** Appel JSON (ou texte/blob) de l'API avec le jeton. Lève ApiError avec un message en français. */
  async function api(path, opts) {
    const o = opts || {};
    const headers = { Accept: o.accept || 'application/json' };
    const token = store.get('kom_token');
    if (token) headers.Authorization = 'Token ' + token;
    let body;
    if (o.form) body = o.form;
    else if (o.body !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(o.body); }
    let res;
    try {
      res = await fetch(API + path, { method: o.method || 'GET', headers, body });
    } catch (e) {
      throw new ApiError(0, null, 'Connexion impossible. Vérifiez votre réseau puis réessayez.');
    }
    const type = res.headers.get('content-type') || '';
    const isJson = type.includes('json');
    if (res.status === 401 && token && !o.keepSession) { logout('expired'); throw new ApiError(401, null, describe(401)); }
    if (!res.ok) {
      let data = null;
      if (isJson) { try { data = await res.json(); } catch (e) { data = null; } }
      if (res.status === 402 && data && data.code === 'subscription_inactive') {
        K.onSubscriptionInactive();
        throw new ApiError(402, data, store.get('kom_role') === 'owner'
          ? 'Abonnement terminé. Abonnez-vous depuis la page Abonnement pour reprendre les ventes.' : INACTIVE_MESSAGE);
      }
      throw new ApiError(res.status, data, describe(res.status, data, isJson));
    }
    if (res.status === 204) return null;
    if (o.text) return res.text();
    if (o.blob) return res.blob();
    if (!isJson) return res.text();
    return res.json();
  }
  K.api = api;

  /** Parcourt toutes les pages d'un endpoint paginé (ou renvoie la liste telle quelle). */
  async function listAll(path, maxPages) {
    let out = [];
    let next = path;
    for (let i = 0; i < (maxPages || 20) && next; i += 1) {
      const data = await api(next);
      if (Array.isArray(data)) return data;
      out = out.concat(data.results || []);
      next = data.next ? data.next.replace(/^https?:\/\/[^/]+\/api\/v1\//, '') : null;
    }
    return out;
  }
  K.listAll = listAll;

  async function download(path, filename) {
    const blob = await api(path, { blob: true, accept: '*/*' });
    const url = URL.createObjectURL(blob);
    const a = h('a', { href: url, download: filename });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }
  K.download = download;

  /** Ouvre un reçu HTML (renvoyé par l'API, protégé par le jeton) dans une fenêtre d'impression. */
  async function printReceipt(saleId) {
    const w = window.open('', '_blank');
    try {
      const html = await api('sales/' + saleId + '/receipt/?format=html', { text: true, accept: 'text/html' });
      if (w) {
        w.document.open();
        w.document.write(html);
        w.document.close();
        w.focus();
        setTimeout(() => { try { w.print(); } catch (e) { /* l'utilisateur imprimera lui-même */ } }, 400);
      } else {
        showReceiptModal(html);
      }
    } catch (e) {
      if (w) w.close();
      toast(e.message, 'error');
    }
  }
  function showReceiptModal(html) {
    openModal('Reçu', (body) => {
      const frame = h('iframe', { class: 'receipt-frame', title: 'Reçu', sandbox: 'allow-modals allow-same-origin' });
      frame.srcdoc = html;
      body.appendChild(frame);
      body.appendChild(h('div', { class: 'btn-row' }, h('button', { type: 'button', class: 'btn', onclick: () => frame.contentWindow.print() }, 'Imprimer')));
    });
  }
  /** Ouvre un document HTML de l'API (reçu d'abonnement) dans un nouvel onglet, sans impression automatique. */
  async function openHtml(path) {
    const w = window.open('', '_blank'); // ouvert tout de suite : sinon le navigateur bloque la fenêtre
    try {
      const html = await api(path, { text: true, accept: 'text/html' });
      if (w) { w.document.open(); w.document.write(html); w.document.close(); w.focus(); } else showReceiptModal(html);
    } catch (e) {
      if (w) w.close();
      toast(e.message, 'error');
    }
  }
  K.openHtml = openHtml;
  K.printReceipt = printReceipt;
  K.showReceiptModal = showReceiptModal;

  /** Téléphone saisi à la congolaise (0990…) → format international (+243990…). */
  function normalizePhone(text) {
    const s = String(text).replace(/[\s\-().]/g, '');
    if (/^0\d{9}$/.test(s)) return '+243' + s.slice(1);
    if (/^243\d{9}$/.test(s)) return '+' + s;
    return s;
  }
  K.normalizePhone = normalizePhone;

  /* ---------- Coquille : menu, boutique, taux ---------- */
  function buildNav(role, page) {
    const list = document.getElementById('nav-list');
    NAV.filter((n) => n.roles.includes(role)).forEach((n) => {
      const ico = h('span', { class: 'ico', 'aria-hidden': 'true' });
      ico.innerHTML = svgIcon(n.id); // SVG statique défini ci-dessus (aucune donnée utilisateur)
      const a = h('a', { href: n.href }, ico, n.label);
      if (n.id === page) a.setAttribute('aria-current', 'page');
      // entrée facultative : cachée tant que l'API du module ne répond pas (ex. sauvegardes : édition locale)
      list.appendChild(h('li', { id: 'nav-' + n.id, hidden: !!n.optional }, a));
    });
    const toggle = document.getElementById('menu-toggle');
    const setOpen = (open) => {
      document.body.classList.toggle('nav-open', open);
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    toggle.addEventListener('click', () => setOpen(!document.body.classList.contains('nav-open')));
    document.getElementById('nav-backdrop').addEventListener('click', () => setOpen(false));
    document.getElementById('logout').addEventListener('click', () => logout());
  }

  function showRateChip(ctx) {
    const chip = document.getElementById('rate-chip');
    if (!chip) return;
    chip.hidden = false;
    chip.textContent = ctx.rate ? '1 $ = ' + fmt(Math.round(parseFloat(ctx.rate)), 'CDF') : 'Taux non défini';
    chip.classList.toggle('chip-warn', !ctx.rate);
    if (canOpen('rates', ctx.role) && document.body.dataset.page !== 'rates') {
      const a = h('a', { href: '/app/taux/', class: 'chip-link', 'aria-label': chip.textContent + ' : ouvrir la page Taux de change' }, chip.textContent);
      chip.textContent = '';
      chip.appendChild(a);
    }
  }

  async function loadRate(ctx) {
    try {
      const r = await api('currency/rates/current/?store=' + encodeURIComponent(ctx.store ? ctx.store.id : 'default'));
      ctx.rate = r.rate_cdf_per_usd;
      ctx.rateScaled = rateScaled(r.rate_cdf_per_usd);
    } catch (e) {
      ctx.rate = null;
      ctx.rateScaled = null;
      if (!(e instanceof ApiError && e.status === 404)) ctx.rateError = e.message;
    }
    showRateChip(ctx);
  }
  K.refreshRate = () => loadRate(K.ctx);

  /* ---------- Bandeau d'abonnement (toutes les pages privées) ---------- */
  function renderBanner(b) {
    const slot = document.getElementById('sub-banner');
    if (!slot) return;
    clear(slot);
    slot.hidden = !b;
    if (!b) return;
    slot.className = 'sub-banner tone-' + b.tone;
    slot.append(h('span', { class: 'sub-banner-text' }, b.text));
    if (b.action) slot.append(h('a', { class: 'btn btn-small sub-banner-btn', href: b.action.href }, b.action.label));
  }
  K.renderBanner = renderBanner;

  /** Relit l'état de l'abonnement (rôles autorisés uniquement) et met à jour le bandeau. */
  async function refreshSubscription() {
    const ctx = K.ctx;
    if (!K.billing.canRead(ctx.role)) return null;
    try {
      ctx.sub = await api('billing/subscription/');
      ctx.inactive = false;
    } catch (e) {
      ctx.sub = null; // module de facturation indisponible : pas de bandeau
    }
    renderBanner(K.billing.bannerFor(ctx.sub, ctx.role));
    return ctx.sub;
  }
  K.refreshSubscription = refreshSubscription;

  /** Une écriture a reçu 402 : l'abonnement est terminé, on l'indique sur toutes les pages. */
  K.onSubscriptionInactive = function () {
    const ctx = K.ctx;
    ctx.inactive = true;
    if (K.billing.canRead(ctx.role)) {
      if (!ctx.sub || ctx.sub.state !== 'read_only') refreshSubscription().then(() => { ctx.inactive = true; });
    } else {
      renderBanner({ tone: 'danger', text: INACTIVE_MESSAGE, action: null });
    }
  };

  /** Sauvegardes locales (propriétaire et gérant) : l'entrée de menu n'apparaît que si l'API répond (édition
   *  locale) ; bandeau d'avertissement sur toutes les pages sauf la page Sauvegardes (qui a son propre état). */
  async function refreshBackups() {
    const ctx = K.ctx;
    ctx.backups = null;
    if (!K.backups.canUse(ctx.role)) return null;
    try {
      ctx.backups = await api('backups/status/');
    } catch (e) {
      ctx.backups = null; // édition en ligne (404) ou module absent : aucune entrée, aucun bandeau
    }
    const item = document.getElementById('nav-backups');
    if (item) item.hidden = !ctx.backups;
    const slot = document.getElementById('backup-banner');
    if (slot) {
      clear(slot);
      const b = document.body.dataset.page === 'backups' ? null : K.backups.bannerFor(ctx.backups);
      slot.hidden = !b;
      if (b) {
        slot.className = 'sub-banner tone-' + b.tone;
        slot.append(h('span', { class: 'sub-banner-text' }, b.text),
          h('a', { class: 'btn btn-small sub-banner-btn', href: b.action.href }, b.action.label));
      }
    }
    return ctx.backups;
  }
  K.refreshBackups = refreshBackups;

  function showWelcome() {
    const area = document.getElementById('sub-area');
    if (!area || !store.get('kom_welcome')) return;
    store.del('kom_welcome');
    const box = h('div', { class: 'alert alert-ok welcome', role: 'status' },
      h('span', null, 'Bienvenue sur Komersia ! Votre essai gratuit de 14 jours commence aujourd’hui : toutes les fonctions du plan Pro sont disponibles, sans carte ni paiement.'),
      h('button', { type: 'button', class: 'btn btn-small btn-ghost', onclick: () => box.remove() }, 'Fermer'));
    area.appendChild(box);
  }

  async function bootPrivate(page, view) {
    const ctx = K.ctx;
    if (!store.get('kom_token')) {
      window.location.replace('/app/connexion/');
      return;
    }
    ctx.role = store.get('kom_role') || 'cashier';
    buildNav(ctx.role, page);
    let me;
    try {
      me = await api('auth/me/');
    } catch (e) {
      clear(view).appendChild(alertBox(e.message));
      return;
    }
    ctx.role = me.role;
    ctx.me = me;
    store.set('kom_role', me.role);
    store.set('kom_name', me.full_name);
    ctx.tenant = me.tenant;
    document.getElementById('user-label').textContent = '';
    const initials = me.full_name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('') || '?';
    document.getElementById('user-label').append(
      h('span', { class: 'avatar', 'aria-hidden': 'true' }, initials),
      h('div', { class: 'user-meta' }, h('strong', null, me.full_name), h('span', null, (ROLE_LABELS[me.role] || me.role) + ' · ' + me.tenant.name)));

    if (!canOpen(page, ctx.role)) {
      clear(view).append(alertBox('Votre rôle (' + (ROLE_LABELS[ctx.role] || ctx.role) + ') n’a pas accès à cette page.', 'warn'),
        h('a', { class: 'btn', href: homeFor(ctx.role) }, 'Aller à ma page d’accueil'));
      return;
    }

    try {
      ctx.stores = (await listAll('stores/')).filter((s) => s.is_active !== false);
    } catch (e) {
      ctx.stores = [];
    }
    const wanted = store.get('kom_store');
    ctx.store = ctx.stores.find((s) => s.id === wanted) || ctx.stores[0] || null;
    if (ctx.store) store.set('kom_store', ctx.store.id);
    const slot = document.getElementById('store-slot');
    if (ctx.stores.length > 1) {
      const sel = K.select(ctx.stores.map((s) => [s.id, s.name]), ctx.store.id, { 'aria-label': 'Boutique' });
      sel.addEventListener('change', () => { store.set('kom_store', sel.value); window.location.reload(); });
      slot.appendChild(sel);
    } else if (ctx.store) {
      slot.appendChild(h('span', { class: 'chip' }, ctx.store.name));
    }
    await Promise.all([loadRate(ctx), refreshSubscription(), refreshBackups()]);
    showWelcome();

    const render = K.pages[page];
    if (!render) { clear(view).appendChild(alertBox('Page inconnue.')); return; }
    clear(view);
    try {
      await render(view, ctx);
    } catch (e) {
      clear(view).appendChild(alertBox(e.message || 'Erreur inattendue.'));
    }
  }

  /* ---------- Pages publiques : connexion et inscription ---------- */
  function saveSession(data) {
    store.set('kom_token', data.token);
    store.set('kom_role', data.role);
    if (data.tenant) store.set('kom_tenant', JSON.stringify(data.tenant));
  }

  function nextUrl(role) {
    const next = new URLSearchParams(window.location.search).get('next');
    return next && next.startsWith('/app/') ? next : homeFor(role);
  }

  K.pages.login = function (view) {
    const errorSlot = h('div');
    if (new URLSearchParams(window.location.search).get('expired')) errorSlot.appendChild(alertBox('Votre session a expiré ou votre compte n’est plus actif. Reconnectez-vous.', 'warn'));
    const phone = h('input', { type: 'tel', name: 'phone', autocomplete: 'username', inputmode: 'tel', required: true, placeholder: '+243 99 000 0000' });
    const password = h('input', { type: 'password', name: 'password', autocomplete: 'current-password', required: true });
    const submit = h('button', { type: 'submit', class: 'btn btn-big' }, 'Se connecter');
    const form = h('form', { class: 'auth-card', novalidate: true },
      h('h1', null, 'Connexion'), errorSlot,
      field('Téléphone', phone), field('Mot de passe', password), submit);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      clear(errorSlot);
      if (!phone.value.trim() || !password.value) { errorSlot.appendChild(alertBox('Saisissez votre téléphone et votre mot de passe.')); return; }
      await busy(submit, async () => {
        try {
          const data = await api('auth/login/', { method: 'POST', body: { phone: normalizePhone(phone.value), password: password.value }, keepSession: true });
          saveSession(data);
          window.location.replace(nextUrl(data.role));
        } catch (err) {
          errorSlot.appendChild(alertBox(err.status === 403
            ? 'Ce compte est désactivé ou n’a plus accès à une entreprise. Contactez le propriétaire du commerce.' : err.message));
        }
      });
    });
    clear(view).append(form, h('p', { class: 'auth-switch' }, 'Pas encore de compte ? ', h('a', { href: '/app/inscription/' }, 'Créer mon commerce')));
  };

  K.pages.register = function (view) {
    const errorSlot = h('div');
    const f = {
      full_name: h('input', { type: 'text', autocomplete: 'name', required: true }),
      phone: h('input', { type: 'tel', autocomplete: 'tel', inputmode: 'tel', required: true, placeholder: '+243 99 000 0000' }),
      email: h('input', { type: 'email', autocomplete: 'email' }),
      password: h('input', { type: 'password', autocomplete: 'new-password', required: true, minlength: '8' }),
      business_name: h('input', { type: 'text', required: true }),
      sector: K.select([['retail', 'Commerce de détail'], ['restaurant', 'Restauration'], ['pharmacy', 'Pharmacie'],
        ['hardware', 'Quincaillerie'], ['salon', 'Salon / beauté'], ['wholesale', 'Grossiste'], ['services', 'Services'], ['other', 'Autre']], 'retail'),
      base_currency: K.select([['CDF', 'Franc congolais (FC)'], ['USD', 'Dollar américain ($)']], 'CDF'),
      address: h('input', { type: 'text', autocomplete: 'street-address' }),
    };
    const submit = h('button', { type: 'submit', class: 'btn btn-big' }, 'Créer mon compte');
    const form = h('form', { class: 'auth-card', novalidate: true },
      h('h1', null, 'Créer mon commerce'),
      h('p', { class: 'hint' }, 'Votre compte propriétaire, votre entreprise et votre première boutique sont créés en une étape.'),
      errorSlot,
      field('Votre nom complet', f.full_name), field('Téléphone', f.phone),
      field('E-mail (facultatif)', f.email),
      field('Mot de passe', f.password, '8 caractères minimum, pas trop simple.'),
      field('Nom de votre commerce', f.business_name), field('Secteur d’activité', f.sector),
      field('Devise principale', f.base_currency), field('Adresse (facultatif)', f.address), submit);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      clear(errorSlot);
      if (!f.full_name.value.trim() || !f.phone.value.trim() || !f.password.value || !f.business_name.value.trim()) {
        errorSlot.appendChild(alertBox('Renseignez votre nom, votre téléphone, un mot de passe et le nom du commerce.'));
        return;
      }
      await busy(submit, async () => {
        try {
          const data = await api('auth/register/', {
            method: 'POST', keepSession: true,
            body: {
              full_name: f.full_name.value.trim(), phone: normalizePhone(f.phone.value), email: f.email.value.trim(),
              password: f.password.value, business_name: f.business_name.value.trim(), sector: f.sector.value,
              base_currency: f.base_currency.value, address: f.address.value.trim(),
            },
          });
          saveSession(data);
          if (data.store) store.set('kom_store', data.store.id);
          store.set('kom_welcome', '1'); // message d'accueil : l'essai de 14 jours vient de démarrer
          window.location.replace('/app/caisse/');
        } catch (err) { errorSlot.appendChild(alertBox(err.message)); }
      });
    });
    clear(view).append(form, h('p', { class: 'auth-switch' }, 'Déjà inscrit ? ', h('a', { href: '/app/connexion/' }, 'Se connecter')));
  };

  /* ---------- Démarrage ---------- */
  document.addEventListener('DOMContentLoaded', () => {
    const page = document.body.dataset.page;
    const view = document.getElementById('view');
    if (document.body.dataset.public === '1') {
      if (store.get('kom_token')) { window.location.replace(homeFor(store.get('kom_role') || 'cashier')); return; }
      K.pages[page](view);
    } else {
      if (page === 'pos') document.body.classList.add('pos-page');
      bootPrivate(page, view);
    }
  });
}());
