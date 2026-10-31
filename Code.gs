/**
 * Le Média Français – Pilotage de l'automatisation
 * À coller dans le Google Sheet « LMF – Pilotage » (Extensions > Apps Script).
 *
 * Rôle : stocker les posts et les sujets, envoyer le mail de validation,
 * recevoir les clics « Publier maintenant » / « Supprimer », garder le jeton Instagram.
 */

const ONGLET_POSTS = 'Posts';
const ONGLET_SUJETS = 'Sujets';
const COLONNES_POSTS = ['ID', 'Type', 'Titre', 'Statut', 'Créé le', 'Publication prévue', 'Images',
  'Légende', 'Vérification', 'Lien source', 'Jeton', 'Lien Instagram', 'Erreur'];
const COLONNES_SUJETS = ['Statut', 'Famille', 'Sujet', 'Couverture', 'Faits', 'Sources', 'Utilisé le'];
const ONGLET_USAGE = 'Usage';
const COLONNES_USAGE = ['Date', 'Modèle', 'Statut'];
const HEURE_RECAP = 21;  // récap quotidien envoyé vers 21 h

function props_() { return PropertiesService.getScriptProperties(); }
function onglet_(nom) { return SpreadsheetApp.getActive().getSheetByName(nom); }

/* ------------------------------------------------------------------ */
/* Installation                                                        */
/* ------------------------------------------------------------------ */
function installer() {
  const ss = SpreadsheetApp.getActive();
  [[ONGLET_POSTS, COLONNES_POSTS], [ONGLET_SUJETS, COLONNES_SUJETS], [ONGLET_USAGE, COLONNES_USAGE]].forEach(([nom, cols]) => {
    let sh = ss.getSheetByName(nom) || ss.insertSheet(nom);
    if (sh.getLastRow() === 0) sh.appendRow(cols);
    sh.getRange(1, 1, 1, cols.length).setFontWeight('bold').setBackground('#061E61').setFontColor('#FFFFFF');
    sh.setFrozenRows(1);
  });
  const sujets = onglet_(ONGLET_SUJETS);
  const regle = SpreadsheetApp.newDataValidation()
    .requireValueInList(['Disponible', 'En cours', 'Utilisé', 'Auto', 'Exclu'], true).build();
  sujets.getRange('A2:A1000').setDataValidation(regle);
  const posts = onglet_(ONGLET_POSTS);
  const regle2 = SpreadsheetApp.newDataValidation()
    .requireValueInList(['En attente', 'À vérifier', 'Validé', 'Supprimé', 'Publication en cours', 'Publié', 'Erreur'], true).build();
  posts.getRange('D2:D5000').setDataValidation(regle2);
  if (!props_().getProperty('SECRET')) props_().setProperty('SECRET', Utilities.getUuid());
  if (!props_().getProperty('EMAIL')) props_().setProperty('EMAIL', Session.getActiveUser().getEmail());
  if (!props_().getProperty('QUOTA_FLASH')) props_().setProperty('QUOTA_FLASH', '');
  if (!props_().getProperty('QUOTA_FLASH_LITE')) props_().setProperty('QUOTA_FLASH_LITE', '');
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'recapQuotidien')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('recapQuotidien').timeBased().everyDays(1).atHour(HEURE_RECAP).create();
  SpreadsheetApp.getUi().alert(
    'Installation terminée.\n\nTon mot de passe SHEET_SECRET (à copier dans GitHub) :\n\n' +
    props_().getProperty('SECRET'));
}

function definirJetonInstagram() {
  const ui = SpreadsheetApp.getUi();
  const r = ui.prompt('Jeton Instagram', 'Colle le jeton (IG_TOKEN) :', ui.ButtonSet.OK_CANCEL);
  if (r.getSelectedButton() === ui.Button.OK) {
    props_().setProperty('IG_TOKEN', r.getResponseText().trim());
    ui.alert('Jeton enregistré.');
  }
}

function afficherSecret() {
  SpreadsheetApp.getUi().alert('SHEET_SECRET :\n\n' + props_().getProperty('SECRET'));
}

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Le Média Français')
    .addItem('1. Installer', 'installer')
    .addItem('2. Définir le jeton Instagram', 'definirJetonInstagram')
    .addItem('Afficher le SHEET_SECRET', 'afficherSecret')
    .addItem('Envoyer le récap maintenant', 'recapQuotidien')
    .addToUi();
}

/* ------------------------------------------------------------------ */
/* API appelée par le programme (GitHub)                               */
/* ------------------------------------------------------------------ */
function doPost(e) {
  let req;
  try { req = JSON.parse(e.postData.contents); } catch (err) { return json_({ ok: false, erreur: 'JSON invalide' }); }
  if (req.secret !== props_().getProperty('SECRET')) return json_({ ok: false, erreur: 'Secret invalide' });
  const verrou = LockService.getScriptLock();
  verrou.waitLock(30000);
  try {
    const actions = {
      getTopic: prendreSujet_, markTopic: marquerSujet_, addTopic: ajouterSujet_,
      listTitles: titresSujets_, usedLinks: liensUtilises_, addPost: ajouterPost_,
      listDue: postsAPublier_, updatePost: majPost_, logUsage: noterUsage_,
      getToken: () => props_().getProperty('IG_TOKEN') || '',
      setToken: (r) => { props_().setProperty('IG_TOKEN', r.token); return true; },
    };
    if (!actions[req.action]) return json_({ ok: false, erreur: 'Action inconnue : ' + req.action });
    return json_({ ok: true, data: actions[req.action](req) });
  } catch (err) {
    return json_({ ok: false, erreur: String(err) });
  } finally {
    verrou.releaseLock();
  }
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

/* --- Sujets --- */
function prendreSujet_() {
  const sh = onglet_(ONGLET_SUJETS);
  const data = sh.getDataRange().getValues();
  for (let i = 1; i < data.length; i++) {
    if (data[i][0] === 'Disponible' && data[i][2]) {
      sh.getRange(i + 1, 1).setValue('En cours');
      return { row: i + 1, famille: data[i][1], sujet: data[i][2], couverture: data[i][3],
               faits: data[i][4], sources: data[i][5] };
    }
  }
  return null;
}

function marquerSujet_(req) {
  if (!req.row) return false;
  const sh = onglet_(ONGLET_SUJETS);
  sh.getRange(req.row, 1).setValue(req.statut);
  if (req.statut === 'Utilisé') sh.getRange(req.row, 7).setValue(new Date());
  return true;
}

function ajouterSujet_(req) {
  const s = req.sujet;
  const sh = onglet_(ONGLET_SUJETS);
  sh.appendRow([s.statut || 'Auto', s.famille || '', s.sujet || '', s.couverture || '', s.faits || '', s.sources || '', '']);
  return sh.getLastRow();
}

function titresSujets_() {
  const data = onglet_(ONGLET_SUJETS).getDataRange().getValues();
  return data.slice(1).map(r => r[2]).filter(String);
}

function liensUtilises_() {
  const data = onglet_(ONGLET_POSTS).getDataRange().getValues();
  return data.slice(1).map(r => r[9]).filter(String);
}

/* --- Posts --- */
function ajouterPost_(req) {
  const p = req.post;
  const statut = p.verification_ok ? 'En attente' : 'À vérifier';
  const jeton = Utilities.getUuid().slice(0, 12);
  onglet_(ONGLET_POSTS).appendRow([p.id, p.type, p.titre, statut, new Date(), new Date(p.publication_prevue),
    JSON.stringify(p.images), p.legende, p.verification, p.lien_source || '', jeton, '', '']);
  envoyerMail_(p, statut, jeton);
  return true;
}

function postsAPublier_() {
  const data = onglet_(ONGLET_POSTS).getDataRange().getValues();
  const maintenant = new Date();
  const dus = [];
  for (let i = 1; i < data.length; i++) {
    const [id, type, titre, statut, , prevue, imgs, legende] = data[i];
    const echu = statut === 'En attente' && prevue && new Date(prevue) <= maintenant;
    if (statut === 'Validé' || echu) dus.push({ id, type, titre, images: JSON.parse(imgs), legende });
  }
  return dus;
}

function majPost_(req) {
  const sh = onglet_(ONGLET_POSTS);
  const data = sh.getDataRange().getValues();
  for (let i = 1; i < data.length; i++) {
    if (data[i][0] === req.id) {
      const c = req.champs || {};
      if (c.statut !== undefined) sh.getRange(i + 1, 4).setValue(c.statut);
      if (c.lien_instagram !== undefined) sh.getRange(i + 1, 12).setValue(c.lien_instagram);
      if (c.erreur !== undefined) sh.getRange(i + 1, 13).setValue(c.erreur);
      if (c.statut === 'Erreur') {
        MailApp.sendEmail(props_().getProperty('EMAIL'), 'LMF · Erreur de publication · ' + data[i][2],
          'Le post ' + req.id + ' n\'a pas pu être publié :\n\n' + (c.erreur || '') +
          '\n\nOuvre le Sheet, corrige si besoin, puis remets le statut sur « Validé » pour réessayer.');
      }
      return true;
    }
  }
  return false;
}

/* ------------------------------------------------------------------ */
/* Mail de validation                                                  */
/* ------------------------------------------------------------------ */
function envoyerMail_(p, statut, jeton) {
  const base = ScriptApp.getService().getUrl();
  const lien = (a) => `${base}?a=${a}&id=${encodeURIComponent(p.id)}&t=${jeton}`;
  const heure = Utilities.formatDate(new Date(p.publication_prevue), 'Europe/Paris', 'HH:mm');
  const aVerifier = statut === 'À vérifier';
  const bandeau = aVerifier
    ? `<p style="background:#FC0700;color:#fff;padding:12px;font-weight:bold">À VÉRIFIER : la relecture automatique a un doute. Ce post ne partira PAS sans ton clic sur « Publier ».</p>
       <pre style="white-space:pre-wrap;background:#f5f5f5;padding:10px;font-size:12px">${echapper_(p.verification)}</pre>`
    : `<p style="color:#555">Publication automatique à <b>${heure}</b> sauf si tu cliques sur « Supprimer ».</p>`;
  const vignettes = p.images.map(u => `<img src="${u}" width="180" style="margin:3px;border-radius:6px">`).join('');
  const html = `
  <div style="font-family:Arial,sans-serif;max-width:640px">
    <h2 style="color:#061E61;margin-bottom:4px">${echapper_(p.type)} · ${echapper_(p.titre)}</h2>
    ${bandeau}
    <p>
      <a href="${lien('valider')}" style="background:#061E61;color:#fff;padding:12px 18px;text-decoration:none;border-radius:6px;font-weight:bold">✅ Publier maintenant</a>
      &nbsp;
      <a href="${lien('supprimer')}" style="background:#FC0700;color:#fff;padding:12px 18px;text-decoration:none;border-radius:6px;font-weight:bold">🗑 Supprimer</a>
    </p>
    <div>${vignettes}</div>
    <h3 style="color:#061E61">Légende</h3>
    <p style="white-space:pre-wrap">${echapper_(p.legende)}</p>
    <p style="color:#999;font-size:12px">Les images sont hébergées sur imgbb pour la publication.</p>
  </div>`;
  const sujet = `LMF · ${aVerifier ? '⚠️ À vérifier' : heure} · ${p.type} · ${p.titre}`.slice(0, 180);
  MailApp.sendEmail({ to: props_().getProperty('EMAIL'), subject: sujet, htmlBody: html });
}

function echapper_(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/* ------------------------------------------------------------------ */
/* Clics depuis le mail                                                */
/* ------------------------------------------------------------------ */
function doGet(e) {
  const a = e.parameter.a, id = e.parameter.id, t = e.parameter.t;
  const sh = onglet_(ONGLET_POSTS);
  const data = sh.getDataRange().getValues();
  for (let i = 1; i < data.length; i++) {
    if (data[i][0] === id) {
      if (data[i][10] !== t) return page_('Lien invalide.');
      const statut = data[i][3];
      if (['Publié', 'Supprimé', 'Publication en cours'].indexOf(statut) >= 0) return page_('Ce post est déjà « ' + statut + ' ».');
      if (a === 'valider') {
        sh.getRange(i + 1, 4).setValue('Validé');
        return page_('✅ Validé. Il sera publié dans les 20 prochaines minutes.');
      }
      if (a === 'supprimer') {
        sh.getRange(i + 1, 4).setValue('Supprimé');
        return page_('🗑 Supprimé. Il ne sera pas publié.');
      }
    }
  }
  return page_('Post introuvable.');
}

function page_(msg) {
  return HtmlService.createHtmlOutput(
    `<div style="font-family:Arial;padding:40px;font-size:22px;color:#061E61">${msg}<br><br>
     <span style="font-size:14px;color:#888">Le Média Français · tu peux fermer cette page.</span></div>`);
}

/* ------------------------------------------------------------------ */
/* Usage de Gemini et récap quotidien                                  */
/* ------------------------------------------------------------------ */
function noterUsage_(req) {
  let sh = onglet_(ONGLET_USAGE);
  if (!sh) { sh = SpreadsheetApp.getActive().insertSheet(ONGLET_USAGE); sh.appendRow(COLONNES_USAGE); }
  sh.appendRow([new Date(), req.modele || '', req.statut || '']);
  return true;
}

function memeJour_(d, ref) {
  if (!(d instanceof Date)) return false;
  const f = x => Utilities.formatDate(x, 'Europe/Paris', 'yyyy-MM-dd');
  return f(d) === f(ref);
}

function recapQuotidien() {
  const auj = new Date();
  const posts = onglet_(ONGLET_POSTS).getDataRange().getValues().slice(1);
  const duJour = posts.filter(r => memeJour_(r[4], auj));
  const compte = {};
  duJour.forEach(r => { compte[r[3]] = (compte[r[3]] || 0) + 1; });
  const lignesPosts = duJour.map(r => {
    const lien = r[11] ? ` · <a href="${r[11]}">voir</a>` : '';
    return `<li><b>${echapper_(r[3])}</b> · ${echapper_(r[1])} · ${echapper_(r[2])}${lien}</li>`;
  }).join('') || '<li>Aucun post créé aujourd\'hui.</li>';

  const sujets = onglet_(ONGLET_SUJETS).getDataRange().getValues().slice(1);
  const dispo = sujets.filter(r => r[0] === 'Disponible').length;
  const alerteSujets = dispo < 6
    ? `<p style="color:#FC0700"><b>Plus que ${dispo} sujets disponibles.</b> Ajoute-en dans l'onglet Sujets, sinon le programme proposera les siens.</p>` : '';

  const usage = (onglet_(ONGLET_USAGE) ? onglet_(ONGLET_USAGE).getDataRange().getValues().slice(1) : [])
    .filter(r => memeJour_(r[0], auj));
  const parModele = {};
  usage.forEach(r => {
    const m = r[1] || '?';
    parModele[m] = parModele[m] || { ok: 0, quota: 0, erreurs: 0 };
    if (r[2] === 'ok') parModele[m].ok++;
    else if (String(r[2]).indexOf('429') >= 0) parModele[m].quota++;
    else parModele[m].erreurs++;
  });
  const limite = m => {
    const v = m.indexOf('lite') >= 0 ? props_().getProperty('QUOTA_FLASH_LITE') : props_().getProperty('QUOTA_FLASH');
    return v ? ` / ${v} par jour` : '';
  };
  const lignesUsage = Object.keys(parModele).map(m => {
    const u = parModele[m];
    const alerte = u.quota ? ` · <span style="color:#FC0700"><b>${u.quota} refus pour quota (429)</b></span>` : '';
    const err = u.erreurs ? ` · ${u.erreurs} autres erreurs` : '';
    return `<li><b>${echapper_(m)}</b> : ${u.ok} appels réussis${limite(m)}${alerte}${err}</li>`;
  }).join('') || '<li>Aucun appel aujourd\'hui.</li>';
  const conseilQuota = Object.values(parModele).some(u => u.quota)
    ? '<p style="color:#FC0700">Le quota gratuit a été atteint aujourd\'hui : certains posts n\'ont peut-être pas été créés. Active la facturation sur le projet de ta clé Gemini, ou baisse le nombre de posts.</p>' : '';

  const jeton = props_().getProperty('IG_TOKEN') ? 'enregistré' : '<b style="color:#FC0700">absent</b>';
  const resume = ['Publié', 'En attente', 'À vérifier', 'Supprimé', 'Erreur']
    .map(k => `${k} : ${compte[k] || 0}`).join(' · ');

  const html = `
  <div style="font-family:Arial,sans-serif;max-width:640px;color:#1D2433">
    <h2 style="color:#061E61">Récap du ${Utilities.formatDate(auj, 'Europe/Paris', 'dd/MM/yyyy')}</h2>
    <p>${resume}</p>
    <ul>${lignesPosts}</ul>
    <h3 style="color:#061E61">Gemini aujourd'hui</h3>
    <ul>${lignesUsage}</ul>
    ${conseilQuota}
    <p style="color:#888;font-size:12px">Quotas exacts : AI Studio > Usage / Rate limits. Pour les afficher ici, renseigne QUOTA_FLASH et QUOTA_FLASH_LITE dans Paramètres du projet > Propriétés du script.</p>
    <h3 style="color:#061E61">Banque de sujets</h3>
    <p>${dispo} sujets disponibles.</p>
    ${alerteSujets}
    <p style="color:#888;font-size:12px">Jeton Instagram : ${jeton}.</p>
  </div>`;
  MailApp.sendEmail({ to: props_().getProperty('EMAIL'), subject: `LMF · Récap du jour · ${resume}`, htmlBody: html });
}
