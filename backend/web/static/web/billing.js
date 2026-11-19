/* Komersia – page Abonnement : état, plans, paiement Mobile Money, changement de plan, résiliation, historique, rappels. */
(function () {
  'use strict';

  const K = window.Komersia;
  const { h, clear, fmt, fmtDate, api, toast, busy } = K;
  const B = K.billing;

  const POLL_MS = 3000; // interrogation du paiement en attente
  const POLL_MAX_MS = 120000; // arrêt après 2 minutes
  const RESUME_MAX_AGE_MS = 10 * 60 * 1000; // un paiement en attente plus ancien n'est pas repris au chargement

  /** Le mode test (confirmation simulée) n'existe qu'avec BILLING_PROVIDER=sandbox : un seul appel, mémorisé.
      L'endpoint répond 400 (corps invalide) en mode test et 404 sinon ; on n'envoie volontairement aucune référence. */
  let sandboxProbe = null;
  function sandboxAvailable() {
    if (!sandboxProbe) {
      sandboxProbe = api('billing/sandbox/confirm/', { method: 'POST', body: { reference: '', status: 'probe' }, keepSession: true })
        .then(() => true, (e) => e instanceof K.ApiError && e.status !== 404 && e.status !== 0 && e.status < 500);
    }
    return sandboxProbe;
  }

  const inDays = (d) => (d <= 0 ? 'aujourd’hui' : d === 1 ? 'demain' : 'dans ' + d + ' jours');
  const PHONE_RE = /^\+?\d{9,15}$/;

  K.pages.billing = async function billing(view, ctx) {
    const owner = ctx.role === 'owner';
    const S = {
      sub: null, plans: [], payments: [], notices: [], plan: null, waiting: null,
      currency: ctx.tenant && ctx.tenant.base_currency === 'USD' ? 'USD' : 'CDF',
      phone: (ctx.me && ctx.me.phone) || '',
      sandbox: false,
    };
    const el = {
      status: h('div'), notices: h('div'), wait: h('div', { 'aria-live': 'polite' }), limits: h('div'),
      plansTitle: h('h2', { id: 'plans-title' }, 'Choisir un plan'), plans: h('div'), pay: h('div'), history: h('div'),
    };
    const planOf = (code) => S.plans.find((p) => p.code === code) || null;
    const planName = (code) => (planOf(code) ? planOf(code).name : code ? code.charAt(0).toUpperCase() + code.slice(1) : '');
    const scrollTo = (node) => { if (node && node.scrollIntoView) node.scrollIntoView({ block: 'start', behavior: 'smooth' }); };

    /* ---------- chargement ---------- */
    async function loadSubscription() {
      S.sub = await api('billing/subscription/');
      ctx.sub = S.sub;
      ctx.inactive = false;
      K.renderBanner(B.bannerFor(S.sub, ctx.role));
    }
    async function loadLists() {
      const [payments, notices] = await Promise.all([
        K.listAll('billing/payments/', 5).catch(() => S.payments),
        K.listAll('billing/notices/?unread=1', 2).catch(() => S.notices),
      ]);
      S.payments = payments;
      S.notices = notices;
    }
    async function reload() {
      await Promise.all([loadSubscription(), loadLists()]);
      renderAll();
    }

    /* ---------- état de l'abonnement ---------- */
    function renderStatus() {
      const sub = S.sub;
      const d = Number(sub.days_left);
      const urgent = sub.state === 'trial' && d <= 3;
      const card = h('section', { class: 'card abo-status s-' + sub.state + (urgent ? ' urgent' : ''), 'aria-labelledby': 'abo-state-title' });
      const title = (text) => h('h2', { id: 'abo-state-title' }, text);
      const actions = h('div', { class: 'btn-row' });
      const choose = (label, code) => h('button', { type: 'button', class: 'btn', onclick: () => (code ? selectPlan(code) : scrollTo(el.plansTitle)) }, label);

      if (sub.state === 'trial') {
        card.append(title('Essai gratuit'), h('div', { class: 'big' }, B.daysLabel(d)),
          h('p', null, 'Fin de l’essai le ' + fmtDate(sub.trial_ends_at) + '. Pendant l’essai, vous profitez des fonctions et des limites du plan Pro, sans paiement.'),
          h('p', { class: 'hint' }, 'Souscrivez avant la fin de l’essai : l’abonnement démarre dès la confirmation du paiement, et sa première échéance tombe un mois plus tard.'));
        if (owner) actions.append(choose('Choisir un plan', null));
      } else if (sub.state === 'active') {
        card.append(title('Abonnement actif'), h('div', { class: 'big' }, 'Plan ' + planName(sub.plan)),
          h('p', null, (sub.cancel_at_period_end ? 'Actif jusqu’au ' : 'Prochaine échéance : ') + fmtDate(sub.current_period_end) + ' (' + inDays(d) + ').'));
        if (sub.pending_plan) card.appendChild(h('p', null, 'Passage au plan ' + planName(sub.pending_plan) + ' le ' + fmtDate(sub.current_period_end) + ', à la fin de la période payée.'));
        if (sub.cancel_at_period_end) {
          card.appendChild(K.alertBox('Résiliation programmée : votre abonnement reste actif jusqu’au ' + fmtDate(sub.current_period_end) + ', puis le compte passera en lecture seule.', 'warn'));
          if (owner) actions.append(h('button', { type: 'button', class: 'btn', onclick: (e) => resume(e.currentTarget) }, 'Reprendre l’abonnement'));
        } else if (owner) {
          if (sub.next_action === 'pay') actions.append(choose('Payer l’échéance', sub.plan));
          actions.append(h('button', { type: 'button', class: 'btn btn-danger-outline', onclick: (e) => cancel(e.currentTarget) }, 'Résilier l’abonnement'));
        }
      } else if (sub.state === 'past_due') {
        card.append(title('Paiement en retard'), h('div', { class: 'big' }, d <= 0 ? 'Dernier jour de grâce' : d === 1 ? '1 jour de grâce' : d + ' jours de grâce'),
          h('p', null, 'Votre échéance est dépassée. Toutes les fonctions restent disponibles jusqu’au ' + fmtDate(sub.grace_ends_at) + '.'),
          h('p', { class: 'hint' }, 'Passé ce délai, le compte passera en lecture seule : consultation et export uniquement, plus de nouvelles ventes.'));
        if (owner) actions.append(choose('Payer maintenant', sub.plan));
      } else {
        card.append(title('Abonnement terminé'),
          h('p', null, 'Votre ' + (sub.plan ? 'abonnement' : 'essai gratuit') + ' est terminé' + (sub.read_only_since ? ' depuis le ' + fmtDate(sub.read_only_since) : '') + '. Le compte est en lecture seule : vous pouvez consulter vos données, les exporter et imprimer les reçus déjà émis, mais plus encaisser de nouvelles ventes.'),
          h('p', { class: 'hint' }, 'Vos données sont conservées. Abonnez-vous pour tout réactiver dès la confirmation du paiement.'));
        if (owner) actions.append(choose('S’abonner', sub.plan || null));
      }
      if (!owner) card.appendChild(h('p', { class: 'hint' }, 'Seul le propriétaire peut souscrire, payer ou résilier. Vous pouvez consulter l’état et l’historique.'));
      if (actions.childNodes.length) card.appendChild(actions);
      clear(el.status).appendChild(card);
    }

    /* ---------- limites ---------- */
    function renderLimits() {
      const l = S.sub.limits || {};
      const row = (label, used, max) => {
        const full = max && used >= max;
        return h('div', null,
          h('div', { class: 'pay-row' }, h('strong', null, label), h('span', null, used + ' sur ' + (max === null || max === undefined ? '—' : max) + (full ? ' (limite atteinte)' : ''))),
          h('div', { class: 'meter' + (full ? ' full' : ''), role: 'img', 'aria-label': label + ' : ' + used + ' sur ' + max },
            h('div', { style: 'width:' + (max ? Math.min(100, Math.round((used / max) * 100)) : 0) + '%' })));
      };
      clear(el.limits).appendChild(h('section', { class: 'card' },
        h('h2', null, 'Limites de votre plan'),
        S.sub.state === 'trial' ? h('p', { class: 'hint' }, 'Pendant l’essai : limites du plan Pro.') : (!S.sub.plan ? null : h('p', { class: 'hint' }, 'Plan ' + planName(S.sub.plan) + '.')),
        row('Boutiques', l.stores_used || 0, l.max_stores), row('Utilisateurs', l.users_used || 0, l.max_users)));
    }

    /* ---------- plans ---------- */
    function modeFor(plan) {
      const sub = S.sub;
      if (sub.state === 'active' && sub.plan) {
        if (plan.code === sub.plan) return 'renew';
        const cur = planOf(sub.plan);
        return cur && plan.price_usd_minor < cur.price_usd_minor ? 'downgrade' : 'upgrade';
      }
      return 'subscribe';
    }
    const waitingBlocks = () => !!(S.waiting && (S.waiting.state === 'pending' || S.waiting.state === 'timeout'));

    function renderPlans() {
      const sub = S.sub;
      const noRate = S.plans.some((p) => p.price_cdf_estimate === null || p.price_cdf_estimate === undefined);
      const cards = S.plans.map((p) => {
        const mode = modeFor(p);
        const current = sub.state === 'active' && sub.plan === p.code;
        const scheduled = sub.pending_plan === p.code;
        const card = h('article', { class: 'card plan-card' + (current ? ' current' : '') + (S.plan === p.code ? ' selected' : ''), 'aria-label': 'Plan ' + p.name },
          h('h3', null, p.name, ' ', current ? h('span', { class: 'badge badge-ok' }, 'Plan actuel') : null, scheduled ? h('span', { class: 'badge badge-warn' }, 'Programmé') : null),
          h('div', { class: 'price' }, fmt(p.price_usd_minor, 'USD'), ' ', h('small', null, '/ mois')),
          p.price_cdf_estimate !== null && p.price_cdf_estimate !== undefined
            ? h('div', { class: 'est' }, '≈ ' + fmt(p.price_cdf_estimate, 'CDF') + ' / mois')
            : h('div', { class: 'est' }, 'Taux non défini'),
          h('ul', null,
            h('li', null, p.max_stores + (p.max_stores > 1 ? ' boutiques' : ' boutique')),
            h('li', null, p.max_users + ' utilisateurs'),
            p.extra_store_price_usd_minor > 0 ? h('li', null, '+ ' + fmt(p.extra_store_price_usd_minor, 'USD') + ' par boutique supplémentaire') : null));
        if (owner) {
          const label = { subscribe: 'Choisir ' + p.name, renew: 'Payer l’échéance', upgrade: 'Passer au plan ' + p.name, downgrade: 'Passer au plan ' + p.name + ' (fin de période)' }[mode];
          if (scheduled) {
            card.appendChild(h('button', { type: 'button', class: 'btn btn-secondary', disabled: waitingBlocks(), onclick: (e) => keepCurrentPlan(e.currentTarget) }, 'Annuler le changement'));
          } else if (mode !== 'renew' || sub.next_action === 'pay') {
            card.appendChild(h('button', { type: 'button', class: 'btn' + (mode === 'downgrade' ? ' btn-secondary' : ''), disabled: waitingBlocks(), onclick: () => selectPlan(p.code) }, label));
          }
        }
        return card;
      });
      clear(el.plans).append(
        h('div', { class: 'plans' }, cards),
        noRate ? h('p', { class: 'hint' }, 'Taux de change non défini : les estimations en francs sont indisponibles. ', h('a', { href: '/app/taux/' }, 'Définir le taux dans la page Taux de change'), ' ou payez en dollars.') : null);
    }

    function selectPlan(code) {
      if (!code || !planOf(code)) { scrollTo(el.plansTitle); return; }
      S.plan = code;
      renderPlans();
      renderPay();
      scrollTo(el.pay);
    }

    /* ---------- paiement / changement ---------- */
    function renderPay() {
      clear(el.pay);
      if (!owner || !S.plan || waitingBlocks()) return;
      const plan = planOf(S.plan);
      if (!plan) return;
      const mode = modeFor(plan);
      const sub = S.sub;
      const err = h('div');
      const close = h('button', { type: 'button', class: 'btn btn-ghost', onclick: () => { S.plan = null; renderPlans(); renderPay(); } }, 'Annuler');
      const box = h('section', { class: 'card pay-box', 'aria-label': 'Paiement' });

      if (mode === 'downgrade') {
        const go = h('button', { type: 'button', class: 'btn' }, 'Confirmer le changement');
        go.addEventListener('click', () => busy(go, async () => {
          clear(err);
          try {
            const res = await api('billing/change-plan/', { method: 'POST', body: { plan: plan.code, currency: 'USD', phone: '' } });
            toast('Le plan ' + plan.name + ' sera appliqué à la fin de la période (' + fmtDate(res.effective_at || sub.current_period_end) + ').', 'ok');
            S.plan = null;
            await reload();
          } catch (e) { err.appendChild(K.alertBox(e.message)); }
        }));
        box.append(h('h2', null, 'Passer au plan ' + plan.name), err,
          K.alertBox('Le changement sera appliqué à la fin de la période en cours, le ' + fmtDate(sub.current_period_end) + '. Aucun paiement maintenant : vous gardez votre plan actuel d’ici là.', 'info'),
          h('div', { class: 'btn-row' }, go, close));
        clear(el.pay).appendChild(box);
        return;
      }

      const usdOnly = plan.price_cdf_estimate === null || plan.price_cdf_estimate === undefined;
      if (usdOnly && S.currency === 'CDF') S.currency = 'USD';
      const phone = h('input', { type: 'tel', inputmode: 'tel', autocomplete: 'tel', value: S.phone, placeholder: '+243 99 000 0000' });
      const toggle = h('div', { class: 'mode-toggle', role: 'group', 'aria-label': 'Devise du paiement' });
      const go = h('button', { type: 'button', class: 'btn btn-big' });
      const amountFor = (cur) => (cur === 'USD' ? fmt(plan.price_usd_minor, 'USD') : '≈ ' + fmt(plan.price_cdf_estimate, 'CDF'));
      function refreshToggle() {
        clear(toggle);
        [['CDF', 'Francs congolais'], ['USD', 'Dollars américains']].forEach(([cur, label]) => {
          const disabled = cur === 'CDF' && usdOnly;
          toggle.appendChild(h('button', {
            type: 'button', 'aria-pressed': S.currency === cur ? 'true' : 'false', disabled,
            onclick: () => { S.currency = cur; refreshToggle(); },
          }, h('span', null, label), h('br'), h('small', null, disabled ? 'Taux non défini' : (mode === 'upgrade' ? cur : amountFor(cur)))));
        });
        go.textContent = mode === 'upgrade' ? 'Payer le complément' : 'Payer ' + (S.currency === 'USD' ? fmt(plan.price_usd_minor, 'USD') : fmt(plan.price_cdf_estimate, 'CDF'));
      }
      refreshToggle();
      go.addEventListener('click', () => {
        clear(err);
        const num = K.normalizePhone(phone.value);
        if (!PHONE_RE.test(num)) { err.appendChild(K.alertBox('Saisissez un numéro Mobile Money valide, par exemple +243 99 000 0000.')); phone.focus(); return; }
        S.phone = num;
        busy(go, async () => {
          try {
            const body = { plan: plan.code, currency: S.currency, phone: num };
            const res = await api(mode === 'upgrade' ? 'billing/change-plan/' : 'billing/subscribe/', { method: 'POST', body });
            if (!res.payment) { toast('Plan mis à jour.', 'ok'); S.plan = null; await reload(); return; }
            S.plan = null;
            startWaiting(res.payment, num);
          } catch (e) {
            err.appendChild(K.alertBox(e.message));
            if (e.data && e.data.code === 'no_exchange_rate') err.appendChild(h('a', { class: 'btn btn-secondary', href: '/app/taux/' }, 'Définir le taux de change'));
          }
        });
      });

      const intro = mode === 'upgrade'
        ? h('p', null, 'Vous passez immédiatement au plan ' + plan.name + ' (' + fmt(plan.price_usd_minor, 'USD') + ' par mois). Vous ne payez maintenant que le complément, calculé au prorata du temps restant jusqu’au ' + fmtDate(sub.current_period_end) + ' : son montant exact s’affiche à l’étape suivante, avant votre validation sur le téléphone.')
        : h('p', null, 'Plan ' + plan.name + ' : ' + fmt(plan.price_usd_minor, 'USD') + ' pour un mois' + (mode === 'renew' ? ', à compter de l’échéance actuelle' : ', à compter de la confirmation du paiement') + '.');
      box.append(h('h2', null, mode === 'upgrade' ? 'Passer au plan ' + plan.name : mode === 'renew' ? 'Payer l’échéance' : 'Payer le plan ' + plan.name), intro, err,
        h('div', { class: 'field' }, h('label', null, 'Payer en'), toggle,
          S.currency === 'CDF' && mode !== 'upgrade' ? h('div', { class: 'hint' }, 'Montant en francs calculé au taux du jour, figé à la création du paiement.') : null),
        K.field('Numéro Mobile Money', phone, 'Vous recevrez une demande de confirmation sur ce numéro (M-Pesa, Airtel Money, Orange Money…).'),
        h('div', { class: 'btn-row' }, go, close));
      clear(el.pay).appendChild(box);
    }

    /* ---------- attente de confirmation ---------- */
    function stopTimer() {
      if (S.waiting && S.waiting.timer) { clearInterval(S.waiting.timer); S.waiting.timer = null; }
    }
    function startWaiting(payment, phone) {
      stopTimer();
      const w = { payment, phone, state: 'pending', startedAt: Date.now(), timer: null, busy: false };
      S.waiting = w;
      w.timer = setInterval(tick, POLL_MS);
      renderWaiting();
      renderPlans();
      renderPay();
      scrollTo(el.wait);
      sandboxAvailable().then((ok) => { S.sandbox = ok; if (S.waiting === w) renderWaiting(); });
    }
    async function tick(force) {
      const w = S.waiting;
      if (!w || w.state !== 'pending' || w.busy) return;
      if (!document.body.contains(el.wait)) { stopTimer(); return; }
      if (force !== true && Date.now() - w.startedAt >= POLL_MAX_MS) {
        w.state = 'timeout';
        stopTimer();
        renderWaiting();
        return;
      }
      w.busy = true;
      try {
        const list = await K.listAll('billing/payments/', 1);
        const p = list.find((x) => x.id === w.payment.id);
        if (p && p.status !== 'pending') {
          stopTimer();
          w.payment = p;
          w.state = p.status === 'confirmed' ? 'confirmed' : 'failed';
          await reload().catch(() => null);
          renderWaiting();
          renderPlans();
          renderPay();
        }
      } catch (e) { /* nouvelle tentative au prochain passage */ } finally { w.busy = false; }
    }
    async function simulate(status, btn) {
      const w = S.waiting;
      await busy(btn, async () => {
        try {
          await api('billing/sandbox/confirm/', { method: 'POST', body: { reference: w.payment.reference, status }, keepSession: true });
          await tick(true);
        } catch (e) { toast(e.message, 'error'); }
      });
    }

    function renderWaiting() {
      const w = S.waiting;
      clear(el.wait);
      if (!w) return;
      const p = w.payment;
      const amount = fmt(p.amount_minor, p.currency);
      const closeWaiting = () => { stopTimer(); S.waiting = null; renderWaiting(); renderPlans(); renderPay(); };
      const card = h('section', { class: 'card waiting', 'aria-label': 'Paiement en cours' });
      if (w.state === 'confirmed') {
        card.append(h('div', { class: 'success' }, h('div', { class: 'check-mark', 'aria-hidden': 'true' }, '✓')),
          h('h2', null, 'Paiement confirmé'),
          h('p', null, 'Merci ! Votre paiement de ' + amount + ' est confirmé et votre abonnement est à jour.' + (S.sub && S.sub.state === 'active' ? ' Prochaine échéance : ' + fmtDate(S.sub.current_period_end) + '.' : '')),
          h('div', { class: 'btn-row', style: 'justify-content:center' },
            h('button', { type: 'button', class: 'btn', onclick: () => K.openHtml('billing/payments/' + p.id + '/receipt/?format=html') }, 'Ouvrir le reçu'),
            h('button', { type: 'button', class: 'btn btn-ghost', onclick: closeWaiting }, 'Fermer')));
      } else if (w.state === 'failed') {
        card.append(K.alertBox('Le paiement n’a pas abouti' + (p.status === 'expired' ? ' (délai dépassé)' : '') + '. Aucun abonnement n’a été activé pour ce paiement. Vérifiez votre solde et votre numéro, puis réessayez.', 'error'),
          h('div', { class: 'btn-row', style: 'justify-content:center' },
            h('button', { type: 'button', class: 'btn', onclick: closeWaiting }, 'Réessayer')));
      } else {
        card.append(h('div', { class: 'spinner', 'aria-hidden': 'true' }),
          h('h2', null, 'Confirmez le paiement sur votre téléphone'),
          h('div', { class: 'amount-big' }, amount),
          h('p', null, 'Une demande Mobile Money a été envoyée au ' + w.phone + '. Validez-la avec votre code secret. Cette page se met à jour toute seule.'),
          h('p', { class: 'hint' }, 'Référence : ' + p.reference));
        if (w.state === 'timeout') {
          card.append(K.alertBox('Nous n’avons pas encore reçu la confirmation. Si vous avez validé le paiement, patientez un instant puis vérifiez de nouveau ; sinon, fermez et réessayez.', 'warn'),
            h('div', { class: 'btn-row', style: 'justify-content:center' },
              h('button', { type: 'button', class: 'btn', onclick: () => { w.state = 'pending'; w.startedAt = Date.now(); w.timer = setInterval(tick, POLL_MS); renderWaiting(); tick(true); } }, 'Vérifier de nouveau'),
              h('button', { type: 'button', class: 'btn btn-ghost', onclick: closeWaiting }, 'Fermer')));
        } else {
          card.appendChild(h('div', { class: 'btn-row', style: 'justify-content:center' },
            h('button', { type: 'button', class: 'btn btn-ghost btn-small', onclick: closeWaiting }, 'Annuler l’attente')));
        }
        if (S.sandbox && w.state === 'pending') {
          const ok = h('button', { type: 'button', class: 'btn btn-small' }, 'Simuler la confirmation');
          const ko = h('button', { type: 'button', class: 'btn btn-small btn-danger-outline' }, 'Simuler l’échec');
          ok.addEventListener('click', () => simulate('confirmed', ok));
          ko.addEventListener('click', () => simulate('failed', ko));
          card.appendChild(h('div', { class: 'test-box' }, h('strong', null, 'Mode test'),
            h('p', { class: 'hint' }, 'Aucun vrai opérateur n’est branché : simulez la réponse de Mobile Money.'),
            h('div', { class: 'btn-row' }, ok, ko)));
        }
      }
      el.wait.appendChild(card);
    }

    /* ---------- changement de plan, résiliation, reprise ---------- */
    async function keepCurrentPlan(btn) {
      await busy(btn, async () => {
        try {
          await api('billing/change-plan/', { method: 'POST', body: { plan: S.sub.plan, currency: 'USD', phone: '' } });
          toast('Changement de plan annulé : vous gardez le plan ' + planName(S.sub.plan) + '.', 'ok');
          await reload();
        } catch (e) { toast(e.message, 'error'); }
      });
    }
    async function cancel(btn) {
      const end = fmtDate(S.sub.current_period_end);
      const yes = await K.confirm('Résilier votre abonnement ? Il reste actif jusqu’au ' + end + ', puis le compte passera en lecture seule. Vous pourrez reprendre l’abonnement avant cette date.', 'Résilier', true);
      if (!yes) return;
      await busy(btn, async () => {
        try {
          await api('billing/cancel/', { method: 'POST' });
          toast('Résiliation enregistrée : l’abonnement reste actif jusqu’au ' + end + '.', 'ok');
          await reload();
        } catch (e) { toast(e.message, 'error'); }
      });
    }
    async function resume(btn) {
      await busy(btn, async () => {
        try {
          await api('billing/resume/', { method: 'POST' });
          toast('Abonnement repris : il sera renouvelé normalement.', 'ok');
          await reload();
        } catch (e) { toast(e.message, 'error'); }
      });
    }

    /* ---------- historique et rappels ---------- */
    function renderHistory() {
      const rows = S.payments.map((p) => {
        const [label, tone] = B.paymentStatus(p.status);
        const when = p.confirmed_at || p.created_at;
        return h('div', { class: 'row-card' },
          h('div', { class: 'main' },
            h('div', { class: 'title' }, (B.PAYMENT_KINDS[p.kind] || p.kind) + ' · plan ' + planName(p.plan)),
            h('div', { class: 'sub' }, K.fmtDateTime(when) + (p.status === 'confirmed' && p.period_end ? ' · jusqu’au ' + fmtDate(p.period_end) : '')),
            p.currency === 'CDF' && p.amount_usd_minor ? h('div', { class: 'sub' }, 'Équivalent ' + fmt(p.amount_usd_minor, 'USD')) : null),
          h('div', { class: 'amount' }, fmt(p.amount_minor, p.currency)),
          h('span', { class: 'badge badge-' + tone }, label),
          p.status === 'confirmed'
            ? h('button', { type: 'button', class: 'btn btn-small btn-secondary', 'aria-label': 'Ouvrir le reçu du ' + K.fmtDate(when), onclick: () => K.openHtml('billing/payments/' + p.id + '/receipt/?format=html') }, 'Reçu')
            : null);
      });
      clear(el.history).appendChild(h('section', { 'aria-label': 'Historique des paiements' },
        h('h2', null, 'Historique des paiements'),
        rows.length ? rows : h('div', { class: 'empty' }, 'Aucun paiement pour l’instant.')));
    }

    function renderNotices() {
      clear(el.notices);
      if (!S.notices.length) return;
      const card = h('section', { class: 'card', 'aria-label': 'Rappels' }, h('h2', null, 'Rappels'));
      S.notices.forEach((n) => {
        const mark = h('button', { type: 'button', class: 'btn btn-small btn-secondary' }, 'Marquer comme lu');
        mark.addEventListener('click', () => busy(mark, async () => {
          try {
            await api('billing/notices/' + n.id + '/read/', { method: 'POST' });
            S.notices = S.notices.filter((x) => x.id !== n.id);
            renderNotices();
          } catch (e) { toast(e.message, 'error'); }
        }));
        card.appendChild(h('div', { class: 'notice-row' },
          h('div', null, h('div', null, B.NOTICE_TEXTS[n.kind] || 'Rappel d’abonnement.'), h('div', { class: 'hint' }, K.fmtDateTime(n.created_at))),
          owner ? mark : null));
      });
      el.notices.appendChild(card);
    }

    function renderAll() {
      renderStatus();
      renderNotices();
      renderLimits();
      renderPlans();
      renderPay();
      renderHistory();
    }

    /* ---------- démarrage ---------- */
    await loadSubscription();
    S.plans = await api('billing/plans/');
    await loadLists();
    clear(view).append(el.status, el.notices, el.wait, el.limits, el.plansTitle, el.plans, el.pay, el.history);
    renderAll();
    if (owner) {
      const recent = S.payments.find((p) => p.status === 'pending' && Date.now() - new Date(p.created_at).getTime() < RESUME_MAX_AGE_MS);
      if (recent) startWaiting(recent, recent.phone || S.phone);
    }
  };
}());
