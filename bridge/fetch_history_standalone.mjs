/**
 * Standalone WhatsApp history fetcher using Baileys directly.
 * Connects with existing auth, collects all history sync messages,
 * then calls fetchMessageHistory for active DM chats.
 * Writes results to JSON for SQLite import.
 */
import makeWASocket, {
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  DisconnectReason,
} from '@whiskeysockets/baileys';
import pino from 'pino';
import { writeFileSync, readFileSync } from 'fs';

const AUTH_DIR = '/home/node/.happycapy-whatsapp/whatsapp-auth';
const OUTPUT_FILE = '/tmp/history_messages.json';
const logger = pino({ level: 'silent' });

// Collect all messages here
const allMessages = [];
const chatJids = new Set();
let totalSyncEvents = 0;
let connected = false;

function extractContent(msg) {
  const m = msg.message;
  if (!m) return null;
  if (m.conversation) return m.conversation;
  if (m.extendedTextMessage?.text) return m.extendedTextMessage.text;
  if (m.imageMessage?.caption) return `[Image] ${m.imageMessage.caption}`;
  if (m.videoMessage?.caption) return `[Video] ${m.videoMessage.caption}`;
  if (m.documentMessage?.caption) return `[Document] ${m.documentMessage.caption}`;
  if (m.audioMessage) return '[Voice Message]';
  if (m.imageMessage) return '[Image]';
  if (m.videoMessage) return '[Video]';
  if (m.documentMessage) return '[Document]';
  if (m.stickerMessage) return '[Sticker]';
  return null;
}

function processHistoryMessages(messages, syncType, progress) {
  let stored = 0, skippedGroup = 0, skippedEmpty = 0, skippedShort = 0;
  for (const msg of messages) {
    const content = extractContent(msg);
    if (!content) { skippedEmpty++; continue; }
    const chatJid = msg.key?.remoteJid || '';
    if (chatJid === 'status@broadcast') { skippedEmpty++; continue; }
    if (chatJid.endsWith('@g.us')) { skippedGroup++; continue; }
    if (content.trim().length < 2) { skippedShort++; continue; }

    const ts = Number(msg.messageTimestamp) || 0;
    allMessages.push({
      chatJid,
      content: content.substring(0, 2000),
      timestamp: ts,
      fromMe: msg.key?.fromMe || false,
      pushName: msg.pushName || '',
    });
    stored++;
  }
  totalSyncEvents++;
  const syncNames = { 0: 'INITIAL', 2: 'FULL', 3: 'RECENT', 6: 'ON_DEMAND' };
  const name = syncNames[syncType] || `TYPE_${syncType}`;
  console.log(`[sync] ${name}: stored=${stored}, skip(empty=${skippedEmpty},group=${skippedGroup},short=${skippedShort}), progress=${progress}, total_collected=${allMessages.length}`);
}

async function main() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();
  console.log(`Connecting with Baileys ${version.join('.')}...`);

  const sock = makeWASocket({
    auth: { creds: state.creds, keys: makeCacheableSignalKeyStore(state.keys, logger) },
    version,
    logger,
    printQRInTerminal: false,
    browser: ['HappyCapy-Fetch', 'WhatsApp', '1.0.0'],
    syncFullHistory: true,
    markOnlineOnConnect: false,
  });

  sock.ev.on('creds.update', saveCreds);

  // Collect history sync messages
  sock.ev.on('messaging-history.set', (data) => {
    const { messages, syncType, progress, isLatest } = data;
    if (messages && messages.length > 0) {
      processHistoryMessages(messages, syncType, progress);
    }
    // Save progress after each batch
    saveResults();
  });

  // Collect chat JIDs from chats events
  sock.ev.on('chats.set', (data) => {
    const chats = data || [];
    for (const c of chats) {
      if (c.id && !c.id.endsWith('@g.us') && c.id !== 'status@broadcast') {
        chatJids.add(c.id);
      }
    }
    console.log(`[chats] Received ${chats.length} chats, ${chatJids.size} individual DMs`);
  });
  sock.ev.on('chats.upsert', (chats) => {
    for (const c of chats) {
      if (c.id && !c.id.endsWith('@g.us') && c.id !== 'status@broadcast') {
        chatJids.add(c.id);
      }
    }
  });

  // Handle connection
  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect } = update;
    if (connection === 'open') {
      connected = true;
      console.log('[conn] Connected to WhatsApp!');
      console.log('[conn] Waiting 45s for auto history sync...');

      // Wait for auto sync to complete (shorter - we know it won't come)
      await sleep(10000);
      console.log(`[sync] After auto-sync wait: ${allMessages.length} messages collected, ${totalSyncEvents} sync events`);

      // Hardcoded known JIDs from SQLite + common active chats
      const knownJids = [
        '118709104451836@s.whatsapp.net', '172142943539383@s.whatsapp.net',
        '180440400969830@s.whatsapp.net', '261640935198841@s.whatsapp.net',
        '447831553181@s.whatsapp.net', '70738597961775@s.whatsapp.net',
        '72018196230394@s.whatsapp.net', '85252098105@s.whatsapp.net',
        '85267675551@s.whatsapp.net', '85290416792@s.whatsapp.net',
        '8613702075916@s.whatsapp.net', '919996126890@s.whatsapp.net',
        '923303644638@s.whatsapp.net',
      ];
      for (const j of knownJids) chatJids.add(j);

      // Now try on-demand fetch for known DM chats
      const targetJids = [...chatJids];
      console.log(`[fetch] Will request history for ${targetJids.length} individual DM chats`);

      let requested = 0;
      for (const jid of targetJids) {
        try {
          await sock.fetchMessageHistory(
            100,
            { remoteJid: jid, fromMe: false, id: '' },
            Date.now()
          );
          requested++;
          if (requested % 10 === 0) {
            console.log(`[fetch] Requested ${requested}/${targetJids.length}...`);
          }
          // 2s delay between requests to avoid rate limiting
          await sleep(2000);
        } catch (e) {
          console.log(`[fetch] Error for ${jid}: ${e.message}`);
        }
      }
      console.log(`[fetch] Sent ${requested} on-demand requests. Waiting 30s for responses...`);
      await sleep(30000);

      console.log(`\n=== FINAL RESULTS ===`);
      console.log(`Total messages collected: ${allMessages.length}`);
      console.log(`Total sync events: ${totalSyncEvents}`);
      console.log(`Unique chats: ${new Set(allMessages.map(m => m.chatJid)).size}`);
      saveResults();

      // Disconnect gracefully (do NOT logout - that would delete auth!)
      console.log('[conn] Disconnecting...');
      sock.end(undefined);
      setTimeout(() => process.exit(0), 2000);
    }

    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode;
      console.log(`[conn] Disconnected (code=${code})`);
      if (code === DisconnectReason.loggedOut) {
        console.log('[conn] LOGGED OUT! Auth is invalid.');
        saveResults();
        process.exit(1);
      }
      // Don't auto-reconnect - this is a one-shot script
      saveResults();
      process.exit(0);
    }
  });
}

function saveResults() {
  const uniqueChats = new Set(allMessages.map(m => m.chatJid));
  const result = {
    totalMessages: allMessages.length,
    uniqueChats: uniqueChats.size,
    syncEvents: totalSyncEvents,
    chatJidsDiscovered: [...chatJids],
    messages: allMessages,
  };
  writeFileSync(OUTPUT_FILE, JSON.stringify(result, null, 2));
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

main().catch(e => {
  console.error('Fatal:', e);
  saveResults();
  process.exit(1);
});
