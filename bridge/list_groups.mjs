/**
 * List all WhatsApp groups and their JIDs using existing auth.
 */
import makeWASocket, {
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
} from '@whiskeysockets/baileys';
import pino from 'pino';

const AUTH_DIR = '/home/node/.happycapy-whatsapp/whatsapp-auth';
const logger = pino({ level: 'silent' });

async function main() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();
  console.log(`Connecting with Baileys ${version.join('.')}...`);

  const sock = makeWASocket({
    auth: { creds: state.creds, keys: makeCacheableSignalKeyStore(state.keys, logger) },
    version,
    logger,
    printQRInTerminal: false,
    browser: ['HappyCapy-Groups', 'WhatsApp', '1.0.0'],
    syncFullHistory: false,
    markOnlineOnConnect: false,
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect } = update;
    if (connection === 'open') {
      console.log('[conn] Connected! Fetching groups...\n');

      // Wait a bit for connection to stabilize
      await new Promise(r => setTimeout(r, 3000));

      try {
        const groups = await sock.groupFetchAllParticipating();
        const entries = Object.values(groups);
        console.log(`Found ${entries.length} groups:\n`);

        for (const g of entries) {
          const name = g.subject || '(no name)';
          const jid = g.id;
          const size = g.participants?.length || 0;
          console.log(`  JID: ${jid}`);
          console.log(`  Name: ${name}`);
          console.log(`  Members: ${size}`);
          console.log('');
        }
      } catch (e) {
        console.log(`Error fetching groups: ${e.message}`);
      }

      console.log('[conn] Disconnecting...');
      sock.end(undefined);
      setTimeout(() => process.exit(0), 2000);
    }

    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode;
      console.log(`[conn] Disconnected (code=${code})`);
      process.exit(0);
    }
  });
}

main().catch(e => {
  console.error('Fatal:', e);
  process.exit(1);
});
