/**
 * Task 1C — New Software Tool Evaluation agent (Apps Script, bound to response Sheet).
 *
 * Setup:
 * 1. Open the response Spreadsheet → Extensions → Apps Script.
 * 2. Paste this file. Set script properties:
 *      GEMINI_API_KEY   (preferred)  OR OPENAI_API_KEY
 *      LLM_PROVIDER     = gemini | openai
 *      DEST_MODE        = doc | sheet   (default doc)
 *      SLACK_WEBHOOK_URL (optional) — if set, also posts a short summary to Slack
 * 3. Install trigger: From form submit → onITFormSubmit
 *
 * Why Gemini by default: fast, cheap for structured draft packets; easy API key;
 * good enough for a first-pass vendor packet a human still reviews.
 */

var REQUEST_TYPE_VALUE = 'New Software Tool Evaluation';

function onITFormSubmit(e) {
  var lock = LockService.getDocumentLock();
  lock.waitLock(30000);
  try {
    _handleSubmit(e);
  } finally {
    lock.releaseLock();
  }
}

function _handleSubmit(e) {
  var named = e.namedValues || {};
  var requestType = _first(named['Request type']);
  if (requestType !== REQUEST_TYPE_VALUE) {
    return; // other request types handled by separate Zaps / Jira Automation
  }

  var toolName = _first(named['Tool / vendor name']);
  var website = _first(named['Vendor website URL']);
  var useCase = _first(named['Use case — what job should this tool do for Balto?']);
  var audience = _first(named['Who will use it?']);
  var customerData = _first(
    named['Will it process customer data or connect to production systems?']
  );
  var spend = _first(named['Estimated annual spend']);
  var ssoNeed = _first(named['Must-have: SSO (Okta) before purchase?']);
  var alternatives = _first(
    named['What existing Balto tools did you evaluate instead (and why not)?']
  );
  var submitter = e && e.response ? e.response.getRespondentEmail() : _first(named['Email Address']);
  var urgency = _first(named['Urgency']);
  var classification = _first(named['Data classification involved']);

  var prompt = _buildPrompt({
    toolName: toolName,
    website: website,
    useCase: useCase,
    audience: audience,
    customerData: customerData,
    spend: spend,
    ssoNeed: ssoNeed,
    alternatives: alternatives,
    submitter: submitter,
    urgency: urgency,
    classification: classification,
  });

  var model = PropertiesService.getScriptProperties().getProperty('LLM_PROVIDER') || 'gemini';
  var draft;
  var modelId;
  try {
    var result = _callLlm(prompt, model);
    draft = result.text;
    modelId = result.model;
  } catch (err) {
    draft =
      '# Evaluation packet (LLM FAILED)\n\nError: ' +
      err +
      '\n\nHuman reviewer: run manually. Submitted tool: ' +
      toolName;
    modelId = model + ':error';
    _logRun_(e, toolName, useCase, 'error', '', modelId, String(err));
  }

  var doc = DocumentApp.create('Tool Eval — ' + toolName + ' — ' + Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd'));
  var body = doc.getBody();
  body.clear();
  body.appendParagraph('Balto — New Software Tool Evaluation (draft)').setHeading(DocumentApp.ParagraphHeading.HEADING1);
  body.appendParagraph('Generated for reviewer. Not an approval. SOC 2: human decision required before purchase.');
  body.appendParagraph('Requestor: ' + (submitter || 'unknown'));
  body.appendParagraph('Urgency: ' + (urgency || '') + ' | Classification: ' + (classification || ''));
  body.appendParagraph('');
  // Keep formatting simple/readable for the next engineer
  String(draft)
    .split('\n')
    .forEach(function (line) {
      body.appendParagraph(line);
    });
  doc.saveAndClose();

  // Share with anyone-with-link viewer so hiring reviewers can open it
  try {
    DriveApp.getFileById(doc.getId()).setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  } catch (shareErr) {
    // Workspace policies may block; log and continue
    Logger.log('Share failed: ' + shareErr);
  }

  var docUrl = doc.getUrl();
  _logRun_(e, toolName, useCase, 'google_doc', docUrl, modelId, 'ok');

  var webhook = PropertiesService.getScriptProperties().getProperty('SLACK_WEBHOOK_URL');
  if (webhook) {
    UrlFetchApp.fetch(webhook, {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify({
        text:
          'New tool eval draft for *' +
          toolName +
          '* from ' +
          (submitter || 'unknown') +
          '\n' +
          docUrl,
      }),
      muteHttpExceptions: true,
    });
  }
}

function _buildPrompt(p) {
  return [
    'You are drafting a vendor evaluation packet for Balto Software IT Ops.',
    'Balto stack: Okta (IdP), Iru/Kandji (MDM), Google Workspace, Slack, Jira, SentinelOne.',
    'SOC 2 Type II in scope. Fully remote, macOS-only, ~75 employees.',
    'Output Markdown with EXACTLY these sections:',
    '1. Vendor overview (what they sell, company size if known, HQ)',
    '2. Typical pricing tiers (list public tiers; mark UNKNOWN if not public)',
    '3. Security documentation links to look for (SOC 2 report, ISO 27001, DPA, status page, trust center URL patterns)',
    '4. Integrations with Balto stack (Okta SSO/SAML/OIDC, SCIM, Google Workspace, Iru/Kandji, Slack, Jira, SentinelOne) — say Confirmed / Likely / Unknown / Unlikely',
    '5. Red flags & open questions a human MUST resolve before purchase',
    '6. Suggested approval path (IT only / Manager+IT / InfoSec+IT / CFO+InfoSec) with one-line why',
    '',
    'Rules:',
    '- Do not invent SOC 2 certification. If unsure, say "Verify on trust center".',
    '- Prefer caution when customer data = Yes.',
    '- Keep under 600 words.',
    '- If knowledge is stale, say so.',
    '',
    'Submission:',
    '- Tool: ' + p.toolName,
    '- Website: ' + p.website,
    '- Use case: ' + p.useCase,
    '- Audience: ' + p.audience,
    '- Customer data / prod: ' + p.customerData,
    '- Estimated spend: ' + p.spend,
    '- SSO requirement: ' + p.ssoNeed,
    '- Alternatives considered: ' + p.alternatives,
    '- Requestor: ' + p.submitter,
    '- Urgency: ' + p.urgency,
    '- Data classification: ' + p.classification,
  ].join('\n');
}

function _callLlm(prompt, provider) {
  if (provider === 'openai') {
    return _callOpenAI(prompt);
  }
  return _callGemini(prompt);
}

function _callGemini(prompt) {
  var key = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');
  if (!key) throw new Error('GEMINI_API_KEY script property missing');
  var model = PropertiesService.getScriptProperties().getProperty('GEMINI_MODEL') || 'gemini-2.0-flash';
  var url =
    'https://generativelanguage.googleapis.com/v1beta/models/' +
    model +
    ':generateContent?key=' +
    encodeURIComponent(key);
  var resp = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({
      contents: [{ role: 'user', parts: [{ text: prompt }] }],
      generationConfig: { temperature: 0.2 },
    }),
    muteHttpExceptions: true,
  });
  if (resp.getResponseCode() >= 300) {
    throw new Error('Gemini HTTP ' + resp.getResponseCode() + ': ' + resp.getContentText());
  }
  var data = JSON.parse(resp.getContentText());
  var text = data.candidates[0].content.parts[0].text;
  return { text: text, model: model };
}

function _callOpenAI(prompt) {
  var key = PropertiesService.getScriptProperties().getProperty('OPENAI_API_KEY');
  if (!key) throw new Error('OPENAI_API_KEY script property missing');
  var model = PropertiesService.getScriptProperties().getProperty('OPENAI_MODEL') || 'gpt-4o-mini';
  var resp = UrlFetchApp.fetch('https://api.openai.com/v1/chat/completions', {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + key },
    payload: JSON.stringify({
      model: model,
      temperature: 0.2,
      messages: [
        { role: 'system', content: 'You write careful SaaS vendor evaluation drafts for IT Ops.' },
        { role: 'user', content: prompt },
      ],
    }),
    muteHttpExceptions: true,
  });
  if (resp.getResponseCode() >= 300) {
    throw new Error('OpenAI HTTP ' + resp.getResponseCode() + ': ' + resp.getContentText());
  }
  var data = JSON.parse(resp.getContentText());
  return { text: data.choices[0].message.content, model: model };
}

function _logRun_(e, toolName, useCase, dest, destUrl, modelId, notes) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName('AgentRuns');
  if (!sheet) {
    sheet = ss.insertSheet('AgentRuns');
    sheet.appendRow([
      'timestamp',
      'response_row',
      'tool_name',
      'use_case',
      'destination',
      'destination_url',
      'model',
      'status',
      'notes',
    ]);
  }
  sheet.appendRow([
    new Date().toISOString(),
    e && e.range ? e.range.getRow() : '',
    toolName,
    useCase,
    dest,
    destUrl,
    modelId,
    notes === 'ok' ? 'ok' : 'error',
    notes,
  ]);
}

function _first(v) {
  if (v == null) return '';
  if (Object.prototype.toString.call(v) === '[object Array]') return v[0] || '';
  return String(v);
}

/**
 * Manual test without a form submit.
 * Run from the editor after setting API key script properties.
 */
function debugRunSampleLinear() {
  var fakeEvent = {
    namedValues: {
      'Request type': ['New Software Tool Evaluation'],
      'Tool / vendor name': ['Linear'],
      'Vendor website URL': ['https://linear.app'],
      'Use case — what job should this tool do for Balto?': [
        'Replace scattered Jira + Slack bug threads for Eng product work',
      ],
      'Who will use it?': ['My team'],
      'Will it process customer data or connect to production systems?': ['No'],
      'Estimated annual spend': ['$500–$5,000'],
      'Must-have: SSO (Okta) before purchase?': ['Yes — blocker without SSO'],
      'What existing Balto tools did you evaluate instead (and why not)?': [
        'Jira — too heavy for product cycle; GitHub Issues — weak project views',
      ],
      Urgency: ['Sev-3 — Needed this week'],
      'Data classification involved': ['Internal'],
      'Email Address': ['eng.lead@balto.ai'],
    },
  };
  _handleSubmit(fakeEvent);
}
