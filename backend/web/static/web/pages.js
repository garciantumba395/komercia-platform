/* Komersia – pages de gestion : produits, stock, clients, taux, ventes, tableau de bord, utilisateurs. */
(function () {
  'use strict';

  const K = window.Komersia;
  const { h, clear, field, fmt, fmtMap, parseAmount, toInput, fmtQty, fmtDateTime, api, toast, busy } = K;
  const errorBox = (m) => K.alertBox(m, 'error');
  const PAGE_ROOT = /^https?:\/\/[^/]+\/api\/v1\//;

  const UNITS = [['piece', 'Pièce'], ['kg', 'Kilogramme'], ['litre', 'Litre'], ['box', 'Boîte'], ['meter', 'Mètre'], ['other', 'Autre']];
  /** Unité abrégée, accordée au nombre : « 1 pièce », « 10 pièces », « 8 kg », « 5 L ». */
  function unitLabel(u, qty) {
    const plural = Math.abs(parseFloat(qty)) > 1;
    const map = { piece: ['pièce', 'pièces'], box: ['boîte', 'boîtes'], kg: ['kg', 'kg'], litre: ['L', 'L'], meter: ['m', 'm'], other: ['', ''] };
    const pair = map[u];
    return pair ? pair[plural ? 1 : 0] : String(u || '');
  }
  const KIND_LABELS = { in: 'Entrée', out: 'Sortie', adjustment: 'Ajustement', sale: 'Vente', return: 'Retour' };

  const unavailable = (e) => e instanceof K.ApiError && e.status === 404 && !e.data;

  function tabs(items, onSelect) {
    const bar = h('div', { class: 'tabs', role: 'tablist' });
    const buttons = items.map(([id, label], i) => {
      const b = h('button', { type: 'button', class: 'btn btn-ghost', role: 'tab', 'aria-selected': i === 0 ? 'true' : 'false' }, label);
      b.addEventListener('click', () => {
        buttons.forEach((x) => x.setAttribute('aria-selected', x === b ? 'true' : 'false'));
        onSelect(id);
      });
      return b;
    });
    buttons.forEach((b) => bar.appendChild(b));
    return bar;
  }

  /** Sélecteur de produit à recherche (stock, mouvements). */
  function productPicker(onPick, onlyTracked) {
    const input = h('input', { type: 'search', placeholder: 'Nom, SKU ou code-barres', autocomplete: 'off', 'aria-label': 'Rechercher un produit' });
    const results = h('div');
    const go = K.debounce(async () => {
      clear(results);
      const q = input.value.trim();
      if (!q) return;
      try {
        const data = await api('catalog/products/?active=1&q=' + encodeURIComponent(q));
        const list = data.results.filter((p) => !onlyTracked || p.track_stock);
        if (!list.length) { results.appendChild(h('div', { class: 'hint' }, 'Aucun produit trouvé (seuls les produits avec suivi de stock sont proposés).')); return; }
        results.appendChild(h('div', { class: 'customer-results' }, list.slice(0, 10).map((p) => h('button', { type: 'button', onclick: () => onPick(p) },
          h('strong', null, p.name), h('div', { class: 'hint' }, p.sku)))));
      } catch (e) { results.appendChild(errorBox(e.message)); }
    }, 250);
    input.addEventListener('input', go);
    return h('div', { class: 'field' }, h('label', { for: 'pp' }, 'Produit'), Object.assign(input, { id: 'pp' }), results);
  }

  /* ======================= PRODUITS ======================= */
  K.pages.products = async function (view, ctx) {
    const canWrite = K.canWrite.products(ctx.role);
    const canExport = ['owner', 'manager', 'accountant'].includes(ctx.role);
    let categories = [];
    try { categories = await api('catalog/categories/'); } catch (e) { categories = []; }
    const state = { q: '', category: '', next: null, items: [] };
    const search = h('input', { type: 'search', placeholder: 'Rechercher un produit, un SKU, un code-barres', 'aria-label': 'Rechercher un produit', autocomplete: 'off' });
    const catSel = K.select([['', 'Toutes les catégories']].concat(categories.map((c) => [c.id, c.name])), '', { 'aria-label': 'Catégorie' });
    const list = h('div');
    const more = h('div');

    const toolbar = h('div', { class: 'toolbar' }, h('div', { class: 'grow' }, search), h('div', null, catSel));
    const actions = h('div', { class: 'btn-row', style: 'margin-bottom:.8rem' },
      canWrite ? h('button', { type: 'button', class: 'btn', onclick: () => productForm(null) }, '+ Ajouter un produit') : null,
      canWrite ? h('button', { type: 'button', class: 'btn btn-secondary', onclick: importForm }, 'Importer un fichier CSV') : null,
      canExport ? h('button', { type: 'button', class: 'btn btn-secondary', onclick: async (e) => { await busy(e.currentTarget, async () => { try { await K.download('catalog/products/export/', 'produits.csv'); } catch (ex) { toast(ex.message, 'error'); } }); } }, 'Exporter en CSV') : null);
    if (!canWrite) view.appendChild(K.alertBox('Lecture seule : seuls le propriétaire et le gérant modifient le catalogue.', 'info'));
    view.append(actions, toolbar, list, more);

    async function load(reset) {
      let path = state.next;
      if (reset || !path) {
        path = 'catalog/products/?q=' + encodeURIComponent(state.q) + (state.category ? '&category=' + state.category : '');
        state.items = [];
      }
      try {
        const data = await api(path);
        state.items = state.items.concat(data.results);
        state.next = data.next ? data.next.replace(PAGE_ROOT, '') : null;
        render(data.count);
      } catch (e) { clear(list).appendChild(errorBox(e.message)); }
    }
    function render(count) {
      clear(list);
      clear(more);
      if (!state.items.length) { list.appendChild(h('div', { class: 'empty' }, 'Aucun produit.')); return; }
      list.appendChild(h('p', { class: 'hint' }, (count === undefined ? state.items.length : count) + ' produit(s)'));
      state.items.forEach((p) => {
        list.appendChild(h('div', { class: 'row-card' + (canWrite ? ' clickable' : ''), tabindex: canWrite ? '0' : null, role: canWrite ? 'button' : null,
          onclick: canWrite ? () => productForm(p) : null,
          onkeydown: canWrite ? (e) => { if (e.key === 'Enter') productForm(p); } : null },
        h('div', { class: 'main' },
          h('div', { class: 'title' }, p.name, ' ', p.is_active ? null : h('span', { class: 'badge badge-muted' }, 'Inactif'),
            p.kind === 'service' ? h('span', { class: 'badge badge-muted' }, 'Service') : null),
          h('div', { class: 'sub' }, [p.sku, p.barcode, p.category_name, p.track_stock ? 'seuil ' + fmtQty(p.min_stock) : null].filter(Boolean).join(' · ')),
          p.cost_minor !== undefined ? h('div', { class: 'sub' }, 'Coût d’achat : ' + fmt(p.cost_minor, p.currency)) : null),
        h('div', { class: 'amount' }, fmt(p.price_minor, p.currency))));
      });
      if (state.next) more.appendChild(h('button', { type: 'button', class: 'btn btn-secondary btn-block', onclick: () => load(false) }, 'Afficher plus'));
    }
    search.addEventListener('input', K.debounce(() => { state.q = search.value.trim(); load(true); }, 250));
    catSel.addEventListener('change', () => { state.category = catSel.value; load(true); });

    function productForm(p) {
      K.openModal(p ? 'Modifier le produit' : 'Nouveau produit', (body, close) => {
        const f = {
          name: h('input', { type: 'text', value: p ? p.name : '', required: true }),
          sku: h('input', { type: 'text', value: p ? p.sku : '', required: true, autocapitalize: 'characters' }),
          barcode: h('input', { type: 'text', value: p ? p.barcode : '', inputmode: 'numeric', autocomplete: 'off' }),
          category: K.select([['', 'Sans catégorie']].concat(categories.map((c) => [c.id, c.name])).concat([['__new', '+ Nouvelle catégorie…']]), p && p.category ? p.category : ''),
          newCat: h('input', { type: 'text', placeholder: 'Nom de la nouvelle catégorie' }),
          kind: K.select([['product', 'Produit'], ['service', 'Service']], p ? p.kind : 'product'),
          unit: K.select(UNITS, p ? p.unit : 'piece'),
          currency: K.select([['CDF', 'Francs (FC)'], ['USD', 'Dollars ($)']], p ? p.currency : 'CDF'),
          price: h('input', { type: 'text', inputmode: 'decimal', value: p ? toInput(p.price_minor, p.currency) : '' }),
          cost: h('input', { type: 'text', inputmode: 'decimal', value: p && p.cost_minor !== undefined ? toInput(p.cost_minor, p.currency) : '' }),
          min: h('input', { type: 'text', inputmode: 'decimal', value: p ? fmtQty(p.min_stock).replace(/[\s ]/g, '') : '0' }),
          track: h('input', { type: 'checkbox', id: 'pf-track', checked: p ? p.track_stock : true }),
          active: h('input', { type: 'checkbox', id: 'pf-active', checked: p ? p.is_active : true }),
        };
        const showCost = !p || p.cost_minor !== undefined;
        const newCatWrap = h('div', { hidden: true }, field('Nouvelle catégorie', f.newCat));
        f.category.addEventListener('change', () => { newCatWrap.hidden = f.category.value !== '__new'; });
        const err = h('div');
        const btn = h('button', { type: 'submit', class: 'btn btn-block' }, 'Enregistrer');
        const form = h('form', { novalidate: true }, err,
          field('Nom du produit', f.name), h('div', { class: 'grid-2' }, field('SKU (référence)', f.sku), field('Code-barres', f.barcode)),
          field('Catégorie', f.category), newCatWrap,
          h('div', { class: 'grid-2' }, field('Type', f.kind), field('Unité', f.unit)),
          h('div', { class: 'grid-2' }, field('Devise', f.currency), field('Prix de vente', f.price)),
          showCost ? field('Coût d’achat', f.cost) : null,
          h('div', { class: 'grid-2' }, field('Seuil minimum de stock', f.min), h('div')),
          h('div', { class: 'check field' }, f.track, h('label', { for: 'pf-track' }, 'Suivre le stock')),
          h('div', { class: 'check field' }, f.active, h('label', { for: 'pf-active' }, 'Produit actif (vendable)')), btn);
        form.addEventListener('submit', async (e) => {
          e.preventDefault();
          clear(err);
          if (!f.name.value.trim() || !f.sku.value.trim()) { err.appendChild(errorBox('Le nom et le SKU sont obligatoires.')); return; }
          const cur = f.currency.value;
          const price = parseAmount(f.price.value, cur);
          const cost = showCost ? parseAmount(f.cost.value, cur) : 0;
          if (price === null || cost === null) { err.appendChild(errorBox(cur === 'CDF' ? 'Montant invalide : les francs n’ont pas de décimales.' : 'Montant invalide (2 décimales au plus).')); return; }
          const min = f.min.value.trim().replace(',', '.') || '0';
          if (!/^\d+(\.\d{1,3})?$/.test(min)) { err.appendChild(errorBox('Seuil minimum invalide.')); return; }
          await busy(btn, async () => {
            try {
              let category = f.category.value || null;
              if (category === '__new') {
                const name = f.newCat.value.trim();
                if (!name) { err.appendChild(errorBox('Saisissez le nom de la nouvelle catégorie.')); return; }
                const created = await api('catalog/categories/', { method: 'POST', body: { name } });
                categories.push(created);
                category = created.id;
              }
              const payload = {
                name: f.name.value.trim(), sku: f.sku.value.trim(), barcode: f.barcode.value.trim(), category,
                kind: f.kind.value, unit: f.unit.value, currency: cur, price_minor: price,
                track_stock: f.track.checked, min_stock: min, is_active: f.active.checked,
              };
              if (showCost) payload.cost_minor = cost;
              await api(p ? 'catalog/products/' + p.id + '/' : 'catalog/products/', { method: p ? 'PATCH' : 'POST', body: payload });
              toast(p ? 'Produit modifié.' : 'Produit ajouté.', 'ok');
              close();
              load(true);
            } catch (ex) { err.appendChild(errorBox(ex.message)); }
          });
        });
        body.appendChild(form);
      });
    }

    function importForm() {
      K.openModal('Importer des produits (CSV)', (body, close) => {
        const file = h('input', { type: 'file', accept: '.csv,text/csv' });
        const err = h('div');
        const out = h('div');
        const btn = h('button', { type: 'submit', class: 'btn btn-block' }, 'Importer');
        const form = h('form', null,
          h('p', { class: 'hint' }, 'Colonnes attendues : name, sku, barcode, category, kind, unit, price, currency, cost. Seuls « name » et « sku » sont obligatoires. Séparateur : virgule ou point-virgule.'),
          err, field('Fichier CSV', file), btn);
        form.addEventListener('submit', async (e) => {
          e.preventDefault();
          clear(err);
          clear(out);
          if (!file.files.length) { err.appendChild(errorBox('Choisissez un fichier CSV.')); return; }
          const fd = new FormData();
          fd.append('file', file.files[0]);
          await busy(btn, async () => {
            try {
              const rep = await api('catalog/products/import/', { method: 'POST', form: fd });
              out.appendChild(K.alertBox(rep.created + ' produit(s) importé(s), ' + rep.failed + ' ligne(s) en erreur.', rep.failed ? 'warn' : 'ok'));
              rep.rows.filter((r) => r.status === 'error').slice(0, 30).forEach((r) => out.appendChild(h('div', { class: 'hint' }, 'Ligne ' + r.line + ' (' + (r.sku || '?') + ') : ' + r.errors.join(' '))));
              if (rep.created) load(true);
            } catch (ex) { err.appendChild(errorBox(ex.message)); }
          });
        });
        body.append(form, out, h('button', { type: 'button', class: 'btn btn-ghost btn-block', style: 'margin-top:.7rem', onclick: close }, 'Fermer'));
      });
    }
    await load(true);
  };

  /* ======================= STOCK ======================= */
  K.pages.stock = async function (view, ctx) {
    const canWrite = K.canWrite.stock(ctx.role);
    const content = h('div');
    const filter = h('input', { type: 'search', placeholder: 'Filtrer par nom ou SKU', 'aria-label': 'Filtrer les produits', autocomplete: 'off' });
    let current = 'levels';
    let levels = [];

    async function showLevels() {
      clear(content);
      try {
        levels = await K.listAll('stock/levels/?store=' + ctx.store.id);
      } catch (e) { content.appendChild(errorBox(e.message)); return; }
      renderLevels();
    }
    function levelRow(l, withAction) {
      const qty = parseFloat(l.quantity);
      const status = qty <= 0 ? h('span', { class: 'badge badge-danger' }, 'Rupture') : l.is_low ? h('span', { class: 'badge badge-warn' }, 'Stock bas') : null;
      return h('div', { class: 'row-card' },
        h('div', { class: 'main' }, h('div', { class: 'title' }, l.product_name, ' ', status), h('div', { class: 'sub' }, l.sku + ' · seuil minimum ' + fmtQty(l.min_stock))),
        h('div', { class: 'amount' }, (fmtQty(l.quantity) + ' ' + unitLabel(l.unit, l.quantity)).trim()),
        withAction && canWrite ? h('button', { type: 'button', class: 'btn btn-secondary btn-small', onclick: () => movementForm({ id: l.product, name: l.product_name }) }, 'Mouvement') : null);
    }
    function renderLevels() {
      const box = content.querySelector('.lvl') || content.appendChild(h('div', { class: 'lvl' }));
      clear(box);
      const q = filter.value.trim().toLowerCase();
      const rows = levels.filter((l) => !q || l.product_name.toLowerCase().includes(q) || l.sku.toLowerCase().includes(q));
      if (!rows.length) box.appendChild(h('div', { class: 'empty' }, levels.length ? 'Aucun résultat.' : 'Aucun stock enregistré. Faites une première entrée de stock.'));
      rows.forEach((l) => box.appendChild(levelRow(l, true)));
    }
    async function showAlerts() {
      clear(content);
      try {
        const rows = await K.listAll('stock/alerts/low-stock/?store=' + ctx.store.id);
        if (!rows.length) content.appendChild(K.alertBox('Aucune alerte : tous les stocks sont au-dessus du seuil minimum.', 'ok'));
        else content.appendChild(K.alertBox(rows.length + ' produit(s) à réapprovisionner.', 'warn'));
        rows.forEach((l) => content.appendChild(levelRow(l, true)));
      } catch (e) { content.appendChild(errorBox(e.message)); }
    }
    async function showHistory() {
      clear(content);
      try {
        const data = await api('stock/movements/?store=' + ctx.store.id);
        if (!data.results.length) content.appendChild(h('div', { class: 'empty' }, 'Aucun mouvement.'));
        data.results.forEach((m) => content.appendChild(h('div', { class: 'row-card' },
          h('div', { class: 'main' }, h('div', { class: 'title' }, m.product_name), h('div', { class: 'sub' }, [KIND_LABELS[m.kind] || m.kind, fmtDateTime(m.created_at), m.reason, m.reference].filter(Boolean).join(' · '))),
          h('div', { class: 'amount' }, (parseFloat(m.quantity) > 0 ? '+' : '') + fmtQty(m.quantity)))));
      } catch (e) { content.appendChild(errorBox(e.message)); }
    }
    const filterBar = h('div', { class: 'toolbar' }, h('div', { class: 'grow' }, filter));
    const show = (id) => { current = id; filterBar.hidden = id !== 'levels'; ({ levels: showLevels, alerts: showAlerts, history: showHistory })[id](); };

    function movementForm(product) {
      K.openModal('Mouvement de stock', (body, close) => {
        let chosen = product;
        const pickSlot = h('div');
        const chosenLabel = h('div', { class: 'selected-customer', style: 'margin-bottom:.8rem' });
        const kind = K.select([['in', 'Entrée (réception)'], ['out', 'Sortie (casse, perte, usage)'], ['adjustment', 'Ajustement (inventaire)']], 'in');
        const qty = h('input', { type: 'text', inputmode: 'decimal', placeholder: '0' });
        const cost = h('input', { type: 'text', inputmode: 'decimal', placeholder: 'Facultatif' });
        const reason = h('input', { type: 'text', placeholder: 'Ex. : réception fournisseur' });
        const ref = h('input', { type: 'text', placeholder: 'N° de bon de livraison (facultatif)' });
        const qtyField = field('Quantité', qty);
        const costField = field('Coût d’achat unitaire (dans la devise du produit)', cost);
        const err = h('div');
        const btn = h('button', { type: 'submit', class: 'btn btn-block' }, 'Enregistrer');
        function drawChosen() {
          clear(chosenLabel);
          clear(pickSlot);
          if (chosen) {
            chosenLabel.hidden = false;
            chosenLabel.append(h('strong', null, chosen.name), h('button', { type: 'button', class: 'btn btn-ghost btn-small', onclick: () => { chosen = null; drawChosen(); } }, 'Changer'));
          } else {
            chosenLabel.hidden = true;
            pickSlot.appendChild(productPicker((p) => { chosen = p; drawChosen(); }, true));
          }
        }
        const sync = () => {
          costField.hidden = kind.value !== 'in';
          qtyField.querySelector('label').textContent = kind.value === 'adjustment' ? 'Variation (positive ou négative, ex. -3)' : 'Quantité';
        };
        kind.addEventListener('change', sync);
        const form = h('form', { novalidate: true }, err, pickSlot, chosenLabel, field('Type de mouvement', kind), qtyField, costField, field('Motif', reason, 'Obligatoire pour une sortie ou un ajustement.'), field('Référence', ref), btn);
        drawChosen();
        sync();
        form.addEventListener('submit', async (e) => {
          e.preventDefault();
          clear(err);
          if (!chosen) { err.appendChild(errorBox('Choisissez un produit.')); return; }
          const raw = qty.value.trim().replace(',', '.');
          if (!/^-?\d+(\.\d{1,3})?$/.test(raw) || Number(raw) === 0 || (kind.value !== 'adjustment' && raw.startsWith('-'))) {
            err.appendChild(errorBox(kind.value === 'adjustment' ? 'Saisissez une variation non nulle (ex. 5 ou -3).' : 'Saisissez une quantité positive.'));
            return;
          }
          if (kind.value !== 'in' && !reason.value.trim()) { err.appendChild(errorBox('Le motif est obligatoire pour une sortie ou un ajustement (ex. : casse, inventaire).')); reason.focus(); return; }
          const payload = { product: chosen.id, store: ctx.store.id, kind: kind.value, quantity: raw, reason: reason.value.trim(), reference: ref.value.trim() };
          if (kind.value === 'in' && cost.value.trim()) {
            let currency = chosen.currency;
            if (!currency) {
              try { currency = (await api('catalog/products/' + chosen.id + '/')).currency; } catch (ex) { currency = 'CDF'; }
            }
            const c = parseAmount(cost.value, currency);
            if (c === null) { err.appendChild(errorBox('Coût invalide.')); return; }
            payload.unit_cost_minor = c;
          }
          await busy(btn, async () => {
            try {
              await api('stock/movements/', { method: 'POST', body: payload });
              toast('Mouvement enregistré.', 'ok');
              close();
              show(current);
            } catch (ex) { err.appendChild(errorBox(ex.message)); }
          });
        });
        body.appendChild(form);
      });
    }

    const bar = tabs([['levels', 'Niveaux'], ['alerts', 'Alertes'], ['history', 'Historique']], show);
    view.append(
      canWrite ? h('div', { class: 'btn-row', style: 'margin-bottom:.8rem' }, h('button', { type: 'button', class: 'btn', onclick: () => movementForm(null) }, '+ Entrée de stock')) : null,
      bar, filterBar, content);
    filter.addEventListener('input', () => { if (current === 'levels') renderLevels(); });
    await showLevels();
  };

  /* ======================= CLIENTS ET DETTES ======================= */
  K.pages.customers = async function (view, ctx) {
    const canWrite = K.canWrite.customers(ctx.role);
    const content = h('div');
    const search = h('input', { type: 'search', placeholder: 'Nom ou téléphone', 'aria-label': 'Rechercher un client', autocomplete: 'off' });
    const debtOnly = h('input', { type: 'checkbox', id: 'debt-only' });
    let mode = 'clients';

    async function showClients() {
      clear(content);
      const q = search.value.trim();
      try {
        const rows = await K.listAll('customers/customers/?q=' + encodeURIComponent(q) + (debtOnly.checked ? '&with_debt=1' : ''), 6);
        if (!rows.length) content.appendChild(h('div', { class: 'empty' }, 'Aucun client.'));
        rows.forEach((c) => {
          const hasDebt = Object.values(c.balance || {}).some((v) => v > 0);
          content.appendChild(h('div', { class: 'row-card clickable', tabindex: '0', role: 'button', onclick: () => detail(c.id), onkeydown: (e) => { if (e.key === 'Enter') detail(c.id); } },
            h('div', { class: 'main' }, h('div', { class: 'title' }, c.name, ' ', c.is_active ? null : h('span', { class: 'badge badge-muted' }, 'Inactif')),
              h('div', { class: 'sub' }, [c.phone, c.credit_limit_minor ? 'Limite : ' + fmt(c.credit_limit_minor, c.credit_limit_currency) : 'Pas de crédit'].filter(Boolean).join(' · '))),
            hasDebt ? h('span', { class: 'badge badge-warn' }, 'Dette : ' + fmtMap(c.balance)) : h('span', { class: 'badge badge-ok' }, 'À jour')));
        });
      } catch (e) { content.appendChild(errorBox(e.message)); }
    }
    async function showDebts() {
      clear(content);
      try {
        const rows = await api('customers/debts/');
        if (!rows.length) content.appendChild(K.alertBox('Aucune dette en cours.', 'ok'));
        rows.forEach((d) => content.appendChild(h('div', { class: 'row-card clickable', tabindex: '0', role: 'button', onclick: () => detail(d.customer.id), onkeydown: (e) => { if (e.key === 'Enter') detail(d.customer.id); } },
          h('div', { class: 'main' }, h('div', { class: 'title' }, d.customer.name, ' ', d.is_overdue ? h('span', { class: 'badge badge-danger' }, 'En retard' + (d.days_overdue ? ' · ' + d.days_overdue + ' j' : '')) : null),
            h('div', { class: 'sub' }, [d.customer.phone, 'Plus ancienne dette : ' + K.fmtDate(d.oldest_debt_at)].filter(Boolean).join(' · '))),
          h('div', { class: 'amount' }, fmtMap(d.balance)))));
      } catch (e) { content.appendChild(errorBox(e.message)); }
    }
    const refresh = () => (mode === 'clients' ? showClients() : showDebts());

    async function detail(id) {
      let data;
      let customer;
      try {
        [customer, data] = await Promise.all([api('customers/customers/' + id + '/'), api('customers/customers/' + id + '/statement/')]);
      } catch (e) { toast(e.message, 'error'); return; }
      K.openModal(customer.name, (body, close) => build(body, close, customer, data));
    }

    function build(body, close, customer, data) {
      const rebuild = async () => {
        try {
          const [c, s] = await Promise.all([api('customers/customers/' + customer.id + '/'), api('customers/customers/' + customer.id + '/statement/')]);
          clear(body);
          build(body, close, c, s);
        } catch (e) { body.appendChild(errorBox(e.message)); }
        refresh();
      };
      const bal = data.balance || {};
      const owed = ['CDF', 'USD'].filter((c) => bal[c] > 0);
      body.appendChild(h('div', { class: 'card' },
        h('div', null, customer.phone || 'Pas de téléphone'), customer.address ? h('div', { class: 'hint' }, customer.address) : null,
        h('p', null, 'Limite de crédit : ', h('strong', null, customer.credit_limit_minor ? fmt(customer.credit_limit_minor, customer.credit_limit_currency) : 'aucune')),
        h('p', null, 'Dette actuelle : ', h('strong', null, owed.length ? fmtMap(bal) : 'aucune'))));

      if (canWrite && owed.length) {
        const amount = h('input', { type: 'text', inputmode: 'decimal', placeholder: '0' });
        const cur = K.select(owed.map((c) => [c, c === 'CDF' ? 'Francs (FC)' : 'Dollars ($)']), owed[0]);
        const method = K.select([['cash', 'Espèces'], ['mobile_money', 'Mobile Money'], ['other', 'Autre']], 'cash');
        const note = h('input', { type: 'text', placeholder: 'Note (facultatif)' });
        const err = h('div');
        const payAll = h('button', { type: 'button', class: 'btn btn-secondary btn-small', onclick: () => { amount.value = toInput(bal[cur.value], cur.value); amount.focus(); } });
        const syncPayAll = () => { payAll.textContent = 'Solder : ' + fmt(bal[cur.value], cur.value); };
        cur.addEventListener('change', syncPayAll);
        syncPayAll();
        const btn = h('button', { type: 'submit', class: 'btn btn-block' }, 'Encaisser le paiement');
        const form = h('form', { class: 'card', novalidate: true }, h('h3', null, 'Encaisser une dette'), err,
          h('div', { class: 'grid-2' }, field('Montant', amount), field('Devise', cur)), h('div', { class: 'btn-row', style: 'margin-bottom:.7rem' }, payAll), field('Mode de paiement', method), field('Note', note), btn);
        form.addEventListener('submit', async (e) => {
          e.preventDefault();
          clear(err);
          const v = parseAmount(amount.value, cur.value);
          if (!v) { err.appendChild(errorBox('Saisissez un montant valide.')); return; }
          if (v > (bal[cur.value] || 0)) { err.appendChild(errorBox('Le montant dépasse la dette (' + fmt(bal[cur.value] || 0, cur.value) + ').')); return; }
          await busy(btn, async () => {
            try {
              await api('customers/customers/' + customer.id + '/payments/', { method: 'POST', body: { amount_minor: v, currency: cur.value, method: method.value, note: note.value.trim() } });
              toast('Paiement de ' + fmt(v, cur.value) + ' enregistré.', 'ok');
              rebuild();
            } catch (ex) { err.appendChild(errorBox(ex.message)); }
          });
        });
        body.appendChild(form);
      }

      if (canWrite) {
        const edit = h('div', { hidden: true });
        const f = {
          name: h('input', { type: 'text', value: customer.name }), phone: h('input', { type: 'tel', value: customer.phone }),
          address: h('input', { type: 'text', value: customer.address }), notes: h('textarea', null, customer.notes || ''),
          limit: h('input', { type: 'text', inputmode: 'decimal', value: toInput(customer.credit_limit_minor, customer.credit_limit_currency) }),
          lcur: K.select([['CDF', 'Francs (FC)'], ['USD', 'Dollars ($)']], customer.credit_limit_currency),
        };
        f.notes.value = customer.notes || '';
        const err = h('div');
        const btn = h('button', { type: 'submit', class: 'btn btn-block' }, 'Enregistrer');
        const form = h('form', { novalidate: true }, err, field('Nom', f.name), field('Téléphone', f.phone), field('Adresse', f.address), field('Notes', f.notes),
          K.canWrite.limit(ctx.role) ? h('div', { class: 'grid-2' }, field('Limite de crédit', f.limit), field('Devise de la limite', f.lcur)) : null, btn);
        form.addEventListener('submit', async (e) => {
          e.preventDefault();
          clear(err);
          if (!f.name.value.trim()) { err.appendChild(errorBox('Le nom est obligatoire.')); return; }
          const payload = { name: f.name.value.trim(), phone: K.normalizePhone(f.phone.value), address: f.address.value.trim(), notes: f.notes.value };
          if (K.canWrite.limit(ctx.role)) {
            const lim = parseAmount(f.limit.value, f.lcur.value);
            if (lim === null) { err.appendChild(errorBox('Limite invalide.')); return; }
            payload.credit_limit_minor = lim;
            payload.credit_limit_currency = f.lcur.value;
          }
          await busy(btn, async () => {
            try { await api('customers/customers/' + customer.id + '/', { method: 'PATCH', body: payload }); toast('Fiche enregistrée.', 'ok'); rebuild(); } catch (ex) { err.appendChild(errorBox(ex.message)); }
          });
        });
        edit.appendChild(form);
        body.append(h('button', { type: 'button', class: 'btn btn-secondary btn-block', style: 'margin-bottom:.8rem', onclick: () => { edit.hidden = !edit.hidden; } }, 'Modifier la fiche'), edit);
      }

      const entries = (data.entries || []).slice().reverse();
      body.appendChild(h('h3', null, 'Relevé de compte'));
      if (!entries.length) body.appendChild(h('div', { class: 'empty' }, 'Aucune opération.'));
      else {
        body.appendChild(h('div', { class: 'table-wrap' }, h('table', null,
          h('thead', null, h('tr', null, h('th', null, 'Date'), h('th', null, 'Opération'), h('th', { class: 'num' }, 'Montant'))),
          h('tbody', null, entries.map((en) => h('tr', null,
            h('td', null, fmtDateTime(en.created_at)),
            h('td', null, en.kind === 'charge' ? 'Vente à crédit' : 'Paiement' + (en.method ? ' (' + ({ cash: 'espèces', mobile_money: 'Mobile Money', other: 'autre' })[en.method] + ')' : ''), en.note ? h('div', { class: 'hint' }, en.note) : null),
            h('td', { class: 'num ' + (en.kind === 'charge' ? 'diff-neg' : 'diff-pos') }, (en.kind === 'charge' ? '+' : '−') + fmt(en.amount_minor, en.currency))))))));
      }
      body.appendChild(h('div', { class: 'btn-row', style: 'margin-top:.8rem' },
        h('button', { type: 'button', class: 'btn btn-ghost', onclick: () => window.print() }, 'Imprimer le relevé'),
        h('button', { type: 'button', class: 'btn btn-ghost', onclick: close }, 'Fermer')));
    }

    function newCustomer() {
      K.openModal('Nouveau client', (body, close) => {
        const name = h('input', { type: 'text', required: true });
        const phone = h('input', { type: 'tel', inputmode: 'tel' });
        const limit = h('input', { type: 'text', inputmode: 'decimal', placeholder: '0' });
        const lcur = K.select([['CDF', 'Francs (FC)'], ['USD', 'Dollars ($)']], 'CDF');
        const err = h('div');
        const btn = h('button', { type: 'submit', class: 'btn btn-block' }, 'Enregistrer');
        const form = h('form', { novalidate: true }, err, field('Nom', name), field('Téléphone', phone),
          K.canWrite.limit(ctx.role) ? h('div', { class: 'grid-2' }, field('Limite de crédit', limit), field('Devise de la limite', lcur)) : null, btn);
        form.addEventListener('submit', async (e) => {
          e.preventDefault();
          clear(err);
          if (!name.value.trim()) { err.appendChild(errorBox('Le nom est obligatoire.')); return; }
          const payload = { name: name.value.trim(), phone: K.normalizePhone(phone.value) };
          if (K.canWrite.limit(ctx.role)) {
            const lim = parseAmount(limit.value, lcur.value);
            if (lim === null) { err.appendChild(errorBox('Limite invalide.')); return; }
            payload.credit_limit_minor = lim;
            payload.credit_limit_currency = lcur.value;
          }
          await busy(btn, async () => {
            try { await api('customers/customers/', { method: 'POST', body: payload }); toast('Client ajouté.', 'ok'); close(); refresh(); } catch (ex) { err.appendChild(errorBox(ex.message)); }
          });
        });
        body.appendChild(form);
      });
    }

    view.append(
      canWrite ? h('div', { class: 'btn-row', style: 'margin-bottom:.8rem' }, h('button', { type: 'button', class: 'btn', onclick: newCustomer }, '+ Nouveau client')) : null,
      tabs([['clients', 'Clients'], ['debts', 'Dettes à encaisser']], (id) => { mode = id; refresh(); }),
      h('div', { class: 'toolbar' }, h('div', { class: 'grow' }, search), h('div', { class: 'check' }, debtOnly, h('label', { for: 'debt-only' }, 'Avec dette seulement'))), content);
    search.addEventListener('input', K.debounce(() => { if (mode === 'clients') showClients(); }, 250));
    debtOnly.addEventListener('change', () => { if (mode === 'clients') showClients(); });
    await showClients();
  };

  /* ======================= TAUX DE CHANGE ======================= */
  K.pages.rates = async function (view, ctx) {
    const canWrite = K.canWrite.rates(ctx.role);
    const currentBox = h('div', { class: 'card' });
    const history = h('div');

    async function load() {
      clear(currentBox);
      clear(history);
      try {
        const cur = await api('currency/rates/current/?store=' + ctx.store.id);
        const rate = Math.round(parseFloat(cur.rate_cdf_per_usd) * 10000) / 10000;
        currentBox.append(h('h2', null, 'Taux courant'), h('div', { class: 'totals' }, h('div', { class: 'big' }, '1 $ = ' + rate.toLocaleString('fr-FR').replace(/ /g, ' ') + ' FC'),
          h('div', { class: 'alt' }, 'En vigueur depuis le ' + fmtDateTime(cur.effective_at))));
      } catch (e) {
        currentBox.append(h('h2', null, 'Taux courant'), K.alertBox(e.status === 404 ? 'Aucun taux défini. Saisissez le taux du jour ci-dessous.' : e.message, 'warn'));
      }
      try {
        const rows = (await api('currency/rates/?store=default')).results;
        history.appendChild(h('h2', null, 'Historique'));
        if (!rows.length) history.appendChild(h('div', { class: 'empty' }, 'Aucun taux enregistré.'));
        else history.appendChild(h('div', { class: 'table-wrap' }, h('table', null,
          h('thead', null, h('tr', null, h('th', null, 'Date'), h('th', { class: 'num' }, 'Taux (FC pour 1 $)'), h('th', null, 'Note'))),
          h('tbody', null, rows.map((r) => h('tr', null, h('td', null, fmtDateTime(r.effective_at)), h('td', { class: 'num' }, String(parseFloat(r.rate_cdf_per_usd))), h('td', null, r.note || '')))))));
      } catch (e) { history.appendChild(errorBox(e.message)); }
    }

    view.appendChild(currentBox);
    if (canWrite) {
      const rate = h('input', { type: 'text', inputmode: 'decimal', placeholder: '2800' });
      const note = h('input', { type: 'text', placeholder: 'Note (facultatif)' });
      const err = h('div');
      const btn = h('button', { type: 'submit', class: 'btn btn-block' }, 'Enregistrer le nouveau taux');
      const form = h('form', { class: 'card', novalidate: true }, h('h2', null, 'Nouveau taux'), err,
        field('1 dollar vaut combien de francs (FC) ?', rate, 'Le taux est conservé dans l’historique ; les ventes déjà faites ne changent pas.'), field('Note', note), btn);
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        clear(err);
        const v = rate.value.trim().replace(',', '.');
        if (!/^\d+(\.\d{1,4})?$/.test(v) || Number(v) <= 0) { err.appendChild(errorBox('Saisissez un taux valide, par exemple 2800.')); return; }
        await busy(btn, async () => {
          try {
            await api('currency/rates/', { method: 'POST', body: { rate_cdf_per_usd: v, store: null, note: note.value.trim() } });
            toast('Taux enregistré.', 'ok');
            rate.value = '';
            note.value = '';
            await K.refreshRate();
            load();
          } catch (ex) { err.appendChild(errorBox(ex.message)); }
        });
      });
      view.appendChild(form);
    } else view.appendChild(K.alertBox('Lecture seule : seuls le propriétaire et le gérant saisissent le taux.', 'info'));
    view.appendChild(history);
    await load();
  };

  /* ======================= VENTES DU JOUR ======================= */
  K.pages.sales = async function (view, ctx) {
    const canCancel = K.canWrite.cancel(ctx.role);
    const date = h('input', { type: 'date', value: K.todayISO(), 'aria-label': 'Jour affiché' });
    const kpis = h('div');
    const list = h('div');

    const isCancelled = (s) => ['cancelled', 'canceled', 'voided', 'annulee'].includes(String(s.status || '').toLowerCase()) || Boolean(s.cancelled_at || s.is_cancelled);

    async function load() {
      clear(list);
      clear(kpis);
      const day = date.value || K.todayISO();
      let sales;
      try {
        sales = await K.listAll('sales/?store=' + ctx.store.id + '&date=' + day + '&date_from=' + day + '&date_to=' + day, 10);
      } catch (e) {
        list.appendChild(unavailable(e) ? K.alertBox('Le module des ventes n’est pas encore disponible sur ce serveur.', 'warn') : errorBox(e.message));
        return;
      }
      sales = sales.filter((s) => !s.created_at || K.dayOf(s.created_at) === day);
      const totals = {};
      let count = 0;
      sales.filter((s) => !isCancelled(s)).forEach((s) => { count += 1; totals[s.currency] = (totals[s.currency] || 0) + s.total_minor; });
      kpis.appendChild(h('div', { class: 'kpis' },
        h('div', { class: 'kpi' }, h('div', { class: 'k' }, 'Ventes'), h('div', { class: 'v' }, String(count))),
        h('div', { class: 'kpi' }, h('div', { class: 'k' }, 'Chiffre d’affaires'), h('div', { class: 'v' }, fmtMap(totals)))));
      if (!sales.length) { list.appendChild(h('div', { class: 'empty' }, 'Aucune vente ce jour-là.')); return; }
      const names = await customerNames(sales);
      sales.forEach((s) => {
        const cancelled = isCancelled(s);
        const who = s.customer_name || names[s.customer] || '';
        list.appendChild(h('div', { class: 'row-card clickable', tabindex: '0', role: 'button', 'aria-label': 'Ouvrir la vente ' + (s.number || ''),
          onclick: () => detail(s, who), onkeydown: (e) => { if (e.key === 'Enter' && e.target === e.currentTarget) detail(s, who); } },
        h('div', { class: 'main' },
          h('div', { class: 'title' }, 'Vente ' + (s.number || ''), ' ', cancelled ? h('span', { class: 'badge badge-danger' }, 'Annulée') : null, s.credit_minor && !cancelled ? h('span', { class: 'badge badge-warn' }, 'Crédit') : null),
          h('div', { class: 'sub' }, [s.created_at ? fmtDateTime(s.created_at).slice(-5) : '', who].filter(Boolean).join(' · '))),
        h('div', { class: 'amount' + (cancelled ? ' struck' : '') }, fmt(s.total_minor, s.currency)),
        h('div', { class: 'btn-row' },
          h('button', { type: 'button', class: 'btn btn-secondary btn-small', onclick: (e) => { e.stopPropagation(); K.printReceipt(s.id); } }, 'Reçu'),
          canCancel && !cancelled ? h('button', { type: 'button', class: 'btn btn-danger-outline btn-small', onclick: (e) => { e.stopPropagation(); cancel(s); } }, 'Annuler') : null)));
      });
    }

    let customerCache = null;
    async function customerNames(sales) {
      if (!sales.some((s) => s.customer && !s.customer_name)) return {};
      if (!customerCache) {
        customerCache = {};
        try { (await K.listAll('customers/customers/', 10)).forEach((c) => { customerCache[c.id] = c.name; }); } catch (e) { /* le nom du client est un confort */ }
      }
      return customerCache;
    }

    function detail(s, who) {
      const cancelled = isCancelled(s);
      K.openModal('Vente ' + (s.number || ''), (body, close) => {
        const row = (k, v) => (v === null || v === undefined || v === '' ? null : [h('dt', null, k), h('dd', null, v)]);
        const payLabel = { cash: 'Espèces', mobile_money: 'Mobile Money', other: 'Autre' };
        body.appendChild(h('dl', { class: 'detail-grid' },
          row('Date', fmtDateTime(s.created_at)), row('Client', who), row('Caissier', ctx.me && s.cashier === ctx.me.id ? 'Vous' : null),
          row('Statut', cancelled ? 'Annulée' : 'Validée'),
          row('Taux appliqué', s.rate_cdf_per_usd ? '1 $ = ' + fmt(Math.round(parseFloat(s.rate_cdf_per_usd)), 'CDF') + (parseFloat(s.rate_cdf_per_usd) % 1 ? ' (' + parseFloat(s.rate_cdf_per_usd) + ')' : '') : null)));
        if (cancelled) {
          body.appendChild(K.alertBox('Vente annulée' + (s.cancelled_at ? ' le ' + fmtDateTime(s.cancelled_at) : '') + (s.cancel_reason ? ' — motif : ' + s.cancel_reason : '') + '.', 'warn'));
        }
        body.appendChild(h('div', { class: 'table-wrap' }, h('table', null,
          h('thead', null, h('tr', null, h('th', null, 'Article'), h('th', { class: 'num' }, 'Qté'), h('th', { class: 'num' }, 'Total'))),
          h('tbody', null, (s.items || []).map((it) => h('tr', null,
            h('td', null, it.name || '', it.discount_minor ? h('div', { class: 'hint' }, 'Remise : −' + fmt(it.discount_minor, s.currency)) : null),
            h('td', { class: 'num' }, fmtQty(it.quantity)), h('td', { class: 'num' }, fmt(it.line_total_minor, s.currency))))))));
        const lines = [];
        if (s.discount_minor) lines.push(['Remise globale', '−' + fmt(s.discount_minor, s.currency)]);
        lines.push(['Total', fmt(s.total_minor, s.currency)]);
        (s.payments || []).forEach((p) => lines.push(['Payé (' + (payLabel[p.method] || p.method) + ')', fmt(p.amount_minor, p.currency)]));
        if (s.change_minor) lines.push(['Monnaie rendue', fmt(s.change_minor, s.change_currency || s.currency)]);
        if (s.credit_minor) lines.push(['Mis à crédit', fmt(s.credit_minor, s.currency)]);
        body.appendChild(h('dl', { class: 'detail-grid', style: 'margin-top:.8rem' }, lines.map(([k, v]) => [h('dt', null, k), h('dd', null, v)])));
        body.appendChild(h('div', { class: 'btn-row' },
          h('button', { type: 'button', class: 'btn btn-secondary', onclick: () => K.printReceipt(s.id) }, 'Réimprimer le reçu'),
          canCancel && !cancelled ? h('button', { type: 'button', class: 'btn btn-danger-outline', onclick: () => { close(); cancel(s); } }, 'Annuler la vente') : null,
          h('button', { type: 'button', class: 'btn btn-ghost', onclick: close }, 'Fermer')));
      });
    }

    function cancel(s) {
      K.openModal('Annuler la vente ' + (s.number || ''), (body, close) => {
        const reason = h('input', { type: 'text', required: true, placeholder: 'Ex. : erreur de saisie, client a renoncé' });
        const err = h('div');
        const btn = h('button', { type: 'submit', class: 'btn btn-danger btn-block' }, 'Annuler cette vente');
        const form = h('form', { novalidate: true }, h('p', null, 'L’annulation remet le stock' + (s.credit_minor ? ', annule la dette du client' : '') + ' et corrige la caisse. Elle ne peut pas être défaite.'), err, field('Motif (obligatoire)', reason), btn);
        form.addEventListener('submit', async (e) => {
          e.preventDefault();
          clear(err);
          if (!reason.value.trim()) { err.appendChild(errorBox('Le motif est obligatoire.')); return; }
          await busy(btn, async () => {
            try { await api('sales/' + s.id + '/cancel/', { method: 'POST', body: { reason: reason.value.trim() } }); toast('Vente annulée.', 'ok'); close(); load(); } catch (ex) { err.appendChild(errorBox(ex.message)); }
          });
        });
        body.appendChild(form);
      });
    }

    date.addEventListener('change', load);
    view.append(h('div', { class: 'toolbar' }, h('div', null, h('label', { for: 'sales-date', class: 'hint' }, 'Jour'), Object.assign(date, { id: 'sales-date' }))), kpis, list);
    await load();
  };

  /* ======================= TABLEAU DE BORD ======================= */
  K.pages.dashboard = async function (view, ctx) {
    const date = h('input', { type: 'date', value: K.todayISO(), id: 'dash-date' });
    const kpiBox = h('div');
    const chartBox = h('div', { class: 'card chart-card' });
    const topBox = h('div', { class: 'card' });
    const debtsBox = h('div', { class: 'card' });
    const stockBox = h('div', { class: 'card' });
    const first = (ctx.me.full_name || '').split(/\s+/)[0] || '';
    const hour = new Date().getHours();
    const greet = hour < 12 ? 'Bonjour' : (hour < 18 ? 'Bon après-midi' : 'Bonsoir');
    const DAYS = ['dim.', 'lun.', 'mar.', 'mer.', 'jeu.', 'ven.', 'sam.'];
    let chartCurrency = 'CDF';

    const ICO = {
      sale: '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3z"/><path d="M9 8h6M9 12h6"/>',
      cash: '<rect x="3" y="6" width="18" height="12" rx="2.5"/><circle cx="12" cy="12" r="2.6"/>',
      credit: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><path d="M17 8v6M14 11h6"/>',
      margin: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
      plus: '<path d="M12 5v14M5 12h14"/>',
    };
    const icon = (key) => { const s = h('span', { class: 'ico', 'aria-hidden': 'true' }); s.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + ICO[key] + '</svg>'; return s; };
    const mapLines = (m) => {
      const parts = ['CDF', 'USD'].filter((c) => m && m[c]).map((c) => h('div', null, fmt(m[c], c)));
      return parts.length ? parts : [h('div', null, fmt(0, 'CDF'))];
    };
    function kpi(tone, key, label, value) {
      return h('div', { class: 'kpi t-' + tone }, h('div', { class: 'k' }, icon(key), label), h('div', { class: 'v' }, value));
    }
    const compact = (n, c) => {
      const v = c === 'USD' ? n / 100 : n;
      if (v >= 1e6) return (v / 1e6).toFixed(1).replace('.', ',') + ' M';
      if (v >= 1e3) return Math.round(v / 1e3) + ' k';
      return String(Math.round(v));
    };

    function drawChart(rows, selected) {
      clear(chartBox);
      const present = ['CDF', 'USD'].filter((c) => rows.some((r) => r.revenue[c] > 0));
      if (!present.includes(chartCurrency)) chartCurrency = present[0] || 'CDF';
      const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Devise du graphique' });
      if (present.length > 1) {
        ['CDF', 'USD'].forEach((c) => seg.appendChild(h('button', { type: 'button', 'aria-pressed': String(c === chartCurrency), onclick: () => { chartCurrency = c; drawChart(rows, selected); } }, c)));
      }
      chartBox.appendChild(h('div', { class: 'chart-head' }, h('div', null, h('h2', null, 'Ventes des 7 derniers jours'), h('div', { class: 'chart-sub' }, 'Chiffre d’affaires par jour (' + chartCurrency + ')')), present.length > 1 ? seg : null));
      const max = Math.max(1, ...rows.map((r) => r.revenue[chartCurrency] || 0));
      const total = rows.reduce((t, r) => t + (r.revenue[chartCurrency] || 0), 0);
      if (!total) { chartBox.appendChild(h('div', { class: 'empty' }, 'Aucune vente sur les 7 derniers jours.')); return; }
      const bars = h('div', { class: 'bars', role: 'img', 'aria-label': 'Ventes par jour : ' + rows.map((r) => r.date + ' ' + fmt(r.revenue[chartCurrency] || 0, chartCurrency)).join(', ') });
      rows.forEach((r) => {
        const v = r.revenue[chartCurrency] || 0;
        const d = new Date(r.date + 'T12:00:00');
        const pct = v ? Math.max(4, Math.round((v / max) * 100)) : 2;
        bars.appendChild(h('div', { class: 'bar-col' + (r.date === selected ? ' sel' : ''), title: fmt(v, chartCurrency) },
          h('div', { class: 'bar-val' }, v ? compact(v, chartCurrency) : ''),
          h('div', { class: 'bar-track' }, h('div', { class: 'bar' + (r.date === selected ? ' today' : '') + (v ? '' : ' empty'), style: 'height:' + pct + '%' })),
          h('div', { class: 'bar-day' }, h('span', null, DAYS[d.getDay()]), h('b', null, String(d.getDate())))));
      });
      chartBox.appendChild(bars);
      chartBox.appendChild(h('div', { class: 'chart-sub', style: 'margin-top:.6rem' }, 'Total sur la période : ', h('strong', null, fmt(total, chartCurrency))));
    }

    async function loadWeek() {
      clear(chartBox).appendChild(h('div', { class: 'skeleton', style: 'height:230px' }));
      try {
        const r = await api('reports/summary/?period=week&store=' + ctx.store.id);
        drawChart(r.by_day || [], K.todayISO());
      } catch (e) {
        clear(chartBox).hidden = true;
      }
    }

    async function loadReport() {
      clear(kpiBox).appendChild(h('div', { class: 'kpis' }, [1, 2, 3, 4].map(() => h('div', { class: 'skeleton' }))));
      clear(topBox);
      topBox.hidden = false;
      try {
        const r = await api('reports/daily/?store=' + ctx.store.id + '&date=' + (date.value || K.todayISO()));
        clear(kpiBox).appendChild(h('div', { class: 'kpis' },
          kpi('blue', 'sale', 'Ventes', String(r.sales_count !== undefined ? r.sales_count : 0)),
          kpi('green', 'cash', 'Encaissé', mapLines(r.cash_collected)),
          kpi('orange', 'credit', 'Crédit accordé', mapLines(r.credit_given)),
          r.estimated_margin ? kpi('navy', 'margin', 'Marge estimée', mapLines(r.estimated_margin)) : kpi('navy', 'margin', 'Chiffre d’affaires', mapLines(r.revenue))));
        topBox.appendChild(h('div', { class: 'card-head' }, h('h2', null, 'Produits les plus vendus'), h('div', { class: 'chart-sub' }, 'CA du jour : ' + mapLines(r.revenue).map((d) => d.textContent).join(' · '))));
        const top = r.top_products || [];
        if (!top.length) topBox.appendChild(h('div', { class: 'empty' }, 'Aucune vente ce jour-là.'));
        const maxByCur = {};
        top.forEach((t) => { maxByCur[t.currency] = Math.max(maxByCur[t.currency] || 0, t.revenue_minor); });
        top.forEach((t, i) => topBox.appendChild(h('div', { class: 'top-row' },
          h('div', { class: 'top-head' }, h('span', { class: 'top-rank' }, String(i + 1)), h('div', { class: 'top-name' }, t.name), h('div', { class: 'top-amount' }, fmt(t.revenue_minor, t.currency))),
          h('div', { class: 'top-meter', 'aria-hidden': 'true' }, h('div', { style: 'width:' + Math.max(4, Math.round((t.revenue_minor / (maxByCur[t.currency] || 1)) * 100)) + '%' })),
          h('div', { class: 'top-sub' }, fmtQty(t.quantity) + ' vendu(s)'))));
      } catch (e) {
        topBox.hidden = true;
        clear(kpiBox).appendChild(K.alertBox(unavailable(e) || (e.status === 404) ? 'Rapports bientôt disponibles. Les dettes et les ruptures ci-dessous sont déjà à jour.' : e.message, unavailable(e) || e.status === 404 ? 'info' : 'error'));
      }
    }

    async function loadDebts() {
      clear(debtsBox).appendChild(h('div', { class: 'card-head' }, h('h2', null, 'Dettes des clients'), h('a', { href: '/app/clients/' }, 'Voir tout')));
      try {
        const rows = await api('customers/debts/');
        const totals = {};
        rows.forEach((d) => Object.entries(d.balance).forEach(([c, v]) => { totals[c] = (totals[c] || 0) + v; }));
        debtsBox.appendChild(h('div', { class: 'totals' }, h('div', { class: 'big' }, fmtMap(totals)), h('div', { class: 'alt' }, rows.length + ' client(s) endetté(s), dont ' + rows.filter((d) => d.is_overdue).length + ' en retard')));
        rows.slice(0, 5).forEach((d) => debtsBox.appendChild(h('div', { class: 'row-card' }, h('div', { class: 'main' }, h('div', { class: 'title' }, d.customer.name), h('div', { class: 'sub' }, d.is_overdue ? 'En retard' : 'En cours')), h('div', { class: 'amount' }, fmtMap(d.balance)))));
      } catch (e) { debtsBox.appendChild(errorBox(e.message)); }
    }

    async function loadStock() {
      clear(stockBox).appendChild(h('div', { class: 'card-head' }, h('h2', null, 'Ruptures et stocks bas'), h('a', { href: '/app/stock/' }, 'Voir le stock')));
      try {
        const rows = await K.listAll('stock/alerts/low-stock/?store=' + ctx.store.id, 3);
        if (!rows.length) stockBox.appendChild(K.alertBox('Aucun produit sous le seuil minimum.', 'ok'));
        else stockBox.appendChild(K.alertBox(rows.length + ' produit(s) sous le seuil minimum.', 'warn'));
        rows.slice(0, 8).forEach((l) => stockBox.appendChild(h('div', { class: 'row-card' },
          h('div', { class: 'main' }, h('div', { class: 'title' }, l.product_name), h('div', { class: 'sub' }, 'Seuil : ' + fmtQty(l.min_stock))),
          parseFloat(l.quantity) <= 0 ? h('span', { class: 'badge badge-danger' }, 'Rupture') : h('span', { class: 'badge badge-warn' }, (fmtQty(l.quantity) + ' ' + unitLabel(l.unit, l.quantity)).trim()))));
      } catch (e) { stockBox.appendChild(errorBox(e.message)); }
    }

    const longDate = new Date().toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' });
    const quick = h('div', { class: 'quick' });
    [['/app/caisse/', 'sale', 'Nouvelle vente', true, ['owner', 'manager', 'cashier']], ['/app/produits/', 'plus', 'Ajouter un produit', false, ['owner', 'manager', 'storekeeper']],
      ['/app/clients/', 'credit', 'Clients et dettes', false, ['owner', 'manager', 'cashier', 'accountant']]].forEach(([href, key, label, primary, roles]) => {
      if (roles.includes(ctx.role)) quick.appendChild(h('a', { href, class: primary ? 'primary' : '' }, icon(key), label));
    });
    date.addEventListener('change', loadReport);
    view.append(
      h('div', { class: 'dash-head' },
        h('div', { class: 'dash-hello' }, greet + (first ? ', ' + first : '') + ' 👋', h('small', null, longDate.charAt(0).toUpperCase() + longDate.slice(1) + ' · ' + (ctx.store ? ctx.store.name : ''))),
        h('div', { class: 'dash-date' }, h('label', { for: 'dash-date', class: 'hint' }, 'Jour'), date)),
      quick, kpiBox,
      h('div', { class: 'dash-grid' }, h('div', null, chartBox, topBox), h('div', null, debtsBox, stockBox)));
    await Promise.all([loadReport(), loadWeek(), loadDebts(), loadStock()]);
  };

  /* ======================= UTILISATEURS ======================= */
  K.pages.users = async function (view, ctx) {
    const list = h('div');
    const ROLE_CHOICES = [['cashier', 'Caissier'], ['manager', 'Gérant'], ['storekeeper', 'Magasinier'], ['accountant', 'Comptable']];

    async function load() {
      clear(list);
      try {
        const rows = await api('users/');
        rows.forEach((u) => {
          const me = ctx.me && u.id === ctx.me.id;
          list.appendChild(h('div', { class: 'row-card' },
            h('div', { class: 'main' }, h('div', { class: 'title' }, u.full_name, ' ', u.is_active ? null : h('span', { class: 'badge badge-muted' }, 'Désactivé'), me ? h('span', { class: 'badge badge-ok' }, 'Vous') : null),
              h('div', { class: 'sub' }, (K.ROLE_LABELS[u.role] || u.role) + ' · ' + u.phone)),
            me ? null : h('button', {
              type: 'button', class: 'btn btn-small ' + (u.is_active ? 'btn-danger-outline' : 'btn-secondary'),
              onclick: async (e) => {
                if (u.is_active && !(await K.confirm('Désactiver ' + u.full_name + ' ? Cette personne ne pourra plus se connecter.', 'Désactiver', true))) return;
                await busy(e.currentTarget, async () => {
                  try { await api('users/' + u.id + '/', { method: 'PATCH', body: { is_active: !u.is_active } }); toast(u.is_active ? 'Utilisateur désactivé.' : 'Utilisateur réactivé.', 'ok'); load(); } catch (ex) { toast(ex.message, 'error'); }
                });
              },
            }, u.is_active ? 'Désactiver' : 'Réactiver')));
        });
      } catch (e) { list.appendChild(errorBox(e.message)); }
    }

    function create() {
      K.openModal('Nouvel utilisateur', (body, close) => {
        const f = {
          name: h('input', { type: 'text', required: true }), phone: h('input', { type: 'tel', inputmode: 'tel', required: true, placeholder: '+243 99 000 0000' }),
          password: h('input', { type: 'text', autocomplete: 'off', required: true, minlength: '8' }), role: K.select(ROLE_CHOICES, 'cashier'),
        };
        const err = h('div');
        const btn = h('button', { type: 'submit', class: 'btn btn-block' }, 'Créer le compte');
        const form = h('form', { novalidate: true }, err, field('Nom complet', f.name), field('Téléphone (identifiant de connexion)', f.phone),
          field('Mot de passe provisoire', f.password, '8 caractères minimum. Communiquez-le à la personne.'), field('Rôle', f.role), btn);
        form.addEventListener('submit', async (e) => {
          e.preventDefault();
          clear(err);
          if (!f.name.value.trim() || !f.phone.value.trim() || !f.password.value) { err.appendChild(errorBox('Renseignez le nom, le téléphone et le mot de passe.')); return; }
          await busy(btn, async () => {
            try {
              await api('users/', { method: 'POST', body: { full_name: f.name.value.trim(), phone: K.normalizePhone(f.phone.value), password: f.password.value, role: f.role.value } });
              toast('Utilisateur créé.', 'ok');
              close();
              load();
            } catch (ex) { err.appendChild(errorBox(ex.message)); }
          });
        });
        body.appendChild(form);
      });
    }

    view.append(h('div', { class: 'btn-row', style: 'margin-bottom:.8rem' }, h('button', { type: 'button', class: 'btn', onclick: create }, '+ Nouvel utilisateur')), list);
    await load();
  };
}());
