/**
 * WhatsApp client wrapper using Baileys.
 */

import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  downloadMediaMessage,
  proto,
} from '@whiskeysockets/baileys';

import { Boom } from '@hapi/boom';
import qrcode from 'qrcode-terminal';
import pino from 'pino';
import { writeFileSync } from 'fs';
import { join } from 'path';

const VERSION = '1.0.0';

export interface InboundMessage {
  id: string;
  sender: string;
  pn: string;
  pushName: string;           // Sender's self-set WhatsApp display name
  content: string;
  timestamp: number;
  isGroup: boolean;
  fromMe: boolean;
  participant?: string;       // Actual sender JID in group messages
  participantPushName?: string; // Group participant's display name
  mentionedJids?: string[];   // @mentioned JIDs in the message
  groupSubject?: string;      // Group name/subject
  quotedMessageId?: string;   // stanzaId of the quoted/replied-to message
  quotedParticipant?: string; // JID of who sent the quoted message
  quotedContent?: string;     // Text content of the quoted message
  media_base64?: string;
  media_type?: string;
  media_mimetype?: string;
  media_filename?: string;
}

export interface ContactInfo {
  jid: string;
  name?: string;         // Name saved by user in phone book
  notify?: string;       // Contact's self-set display name (pushName)
  verifiedName?: string; // Verified business name
  imgUrl?: string;
}

export interface HistorySyncEvent {
  syncType: number;       // 0=INITIAL_BOOTSTRAP, 2=FULL, 3=RECENT, 6=ON_DEMAND
  messages: Array<{
    id: string;
    chatJid: string;
    content: string;
    timestamp: number;
    fromMe: boolean;
    isGroup: boolean;
    participant?: string;
    pushName?: string;
  }>;
  progress: number | null;
  isLatest: boolean;
}

export interface WhatsAppClientOptions {
  authDir: string;
  onMessage: (msg: InboundMessage) => void;
  onQR: (qr: string) => void;
  onStatus: (status: string) => void;
  onHistorySync?: (event: HistorySyncEvent) => void;
  onContactsSync?: (contacts: ContactInfo[]) => void;
}

export class WhatsAppClient {
  private sock: any = null;
  private options: WhatsAppClientOptions;
  private reconnecting = false;

  constructor(options: WhatsAppClientOptions) {
    this.options = options;
  }

  async connect(): Promise<void> {
    const logger = pino({ level: 'silent' });
    const { state, saveCreds } = await useMultiFileAuthState(this.options.authDir);
    const { version } = await fetchLatestBaileysVersion();

    console.log(`Using Baileys version: ${version.join('.')}`);

    this.sock = makeWASocket({
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, logger),
      },
      version,
      logger,
      printQRInTerminal: false,
      browser: ['HappyCapy', 'WhatsApp', VERSION],
      syncFullHistory: true,
      markOnlineOnConnect: false,
    });

    if (this.sock.ws && typeof this.sock.ws.on === 'function') {
      this.sock.ws.on('error', (err: Error) => {
        console.error('WebSocket error:', err.message);
      });
    }

    this.sock.ev.on('connection.update', async (update: any) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        console.log('\nScan this QR code with WhatsApp (Linked Devices):');
        qrcode.generate(qr, { small: true });
        this.options.onQR(qr);
      }

      if (connection === 'close') {
        const statusCode = (lastDisconnect?.error as Boom)?.output?.statusCode;
        const shouldReconnect = statusCode !== DisconnectReason.loggedOut;

        console.log(`Connection closed. Status: ${statusCode}, Will reconnect: ${shouldReconnect}`);
        this.options.onStatus('disconnected');

        if (shouldReconnect && !this.reconnecting) {
          this.reconnecting = true;
          console.log('Reconnecting in 5 seconds...');
          setTimeout(() => {
            this.reconnecting = false;
            this.connect();
          }, 5000);
        }
      } else if (connection === 'open') {
        console.log('Connected to WhatsApp');
        this.options.onStatus('connected');
      }
    });

    this.sock.ev.on('creds.update', saveCreds);

    // History sync: receive older messages on connection (syncFullHistory: true)
    this.sock.ev.on('messaging-history.set', (data: any) => {
      const { messages, contacts, isLatest, progress, syncType } = data;

      // Forward synced contacts to Python
      if (contacts && contacts.length > 0 && this.options.onContactsSync) {
        const parsed: ContactInfo[] = contacts.map((c: any) => ({
          jid: c.id || '',
          name: c.name || undefined,
          notify: c.notify || undefined,
          verifiedName: c.verifiedName || undefined,
        })).filter((c: ContactInfo) => c.jid);
        if (parsed.length > 0) {
          console.log(`Contact sync: ${parsed.length} contacts from history`);
          this.options.onContactsSync(parsed);
        }
      }

      if (!messages || messages.length === 0) return;

      console.log(`History sync: ${messages.length} msgs, type=${syncType}, progress=${progress}, isLatest=${isLatest}`);

      if (this.options.onHistorySync) {
        const parsed: HistorySyncEvent['messages'] = [];
        for (const msg of messages) {
          const content = this.extractMessageContent(msg);
          if (!content) continue;

          const chatJid = msg.key?.remoteJid || '';
          if (chatJid === 'status@broadcast') continue;

          parsed.push({
            id: msg.key?.id || '',
            chatJid,
            content,
            timestamp: (msg.messageTimestamp as number) || 0,
            fromMe: msg.key?.fromMe || false,
            isGroup: chatJid.endsWith('@g.us'),
            participant: msg.key?.participant || '',
            pushName: msg.pushName || '',
          });
        }

        if (parsed.length > 0) {
          this.options.onHistorySync({
            syncType: syncType || 0,
            messages: parsed,
            progress: progress ?? null,
            isLatest: isLatest || false,
          });
        }
      }
    });

    // Contact updates: Baileys fires this when messages arrive with pushName
    this.sock.ev.on('contacts.update', (updates: any[]) => {
      if (!this.options.onContactsSync) return;
      const parsed: ContactInfo[] = updates
        .filter((u: any) => u.id)
        .map((u: any) => ({
          jid: u.id,
          name: u.name || undefined,
          notify: u.notify || undefined,
          verifiedName: u.verifiedName || undefined,
        }));
      if (parsed.length > 0) {
        this.options.onContactsSync(parsed);
      }
    });

    // Contact upserts: Baileys fires this on full contact additions
    this.sock.ev.on('contacts.upsert', (contacts: any[]) => {
      if (!this.options.onContactsSync) return;
      const parsed: ContactInfo[] = contacts
        .filter((c: any) => c.id)
        .map((c: any) => ({
          jid: c.id,
          name: c.name || undefined,
          notify: c.notify || undefined,
          verifiedName: c.verifiedName || undefined,
        }));
      if (parsed.length > 0) {
        console.log(`Contact upsert: ${parsed.length} contacts`);
        this.options.onContactsSync(parsed);
      }
    });

    this.sock.ev.on('messages.upsert', async ({ messages, type }: { messages: any[]; type: string }) => {
      if (type !== 'notify') return;

      for (const msg of messages) {
        if (msg.key.remoteJid === 'status@broadcast') continue;

        const content = this.extractMessageContent(msg);
        if (!content && !this.hasMedia(msg)) continue;

        const isGroup = msg.key.remoteJid?.endsWith('@g.us') || false;
        const fromMe = msg.key.fromMe || false;

        const inbound: InboundMessage = {
          id: msg.key.id || '',
          sender: msg.key.remoteJid || '',
          pn: msg.key.remoteJidAlt || '',
          pushName: msg.pushName || '',
          content: content || '',
          timestamp: msg.messageTimestamp as number,
          isGroup,
          fromMe,
        };

        // Extract contextInfo (available on multiple message types)
        const contextInfo = msg.message?.extendedTextMessage?.contextInfo
          || msg.message?.imageMessage?.contextInfo
          || msg.message?.videoMessage?.contextInfo
          || msg.message?.documentMessage?.contextInfo
          || msg.message?.audioMessage?.contextInfo;

        // Group-specific metadata: actual sender + @mentions
        if (isGroup) {
          inbound.participant = msg.key.participant || '';
          if (contextInfo?.mentionedJid) {
            inbound.mentionedJids = contextInfo.mentionedJid;
          }
        }

        // Quoted/reply message tracking (works for both DMs and groups)
        if (contextInfo?.quotedMessage) {
          inbound.quotedMessageId = contextInfo.stanzaId || '';
          inbound.quotedParticipant = contextInfo.participant || '';
          const qm = contextInfo.quotedMessage;
          inbound.quotedContent = qm.conversation
            || qm.extendedTextMessage?.text
            || qm.imageMessage?.caption
            || qm.videoMessage?.caption
            || (qm.audioMessage ? '[Voice Message]' : undefined)
            || (qm.imageMessage ? '[Image]' : undefined)
            || (qm.videoMessage ? '[Video]' : undefined)
            || (qm.documentMessage ? '[Document]' : undefined)
            || (qm.stickerMessage ? '[Sticker]' : undefined)
            || '';
        }

        // Extract media if present
        const mediaInfo = await this.extractMedia(msg);
        if (mediaInfo) {
          inbound.media_base64 = mediaInfo.base64;
          inbound.media_type = mediaInfo.type;
          inbound.media_mimetype = mediaInfo.mimetype;
          inbound.media_filename = mediaInfo.filename;
        }

        this.options.onMessage(inbound);
      }
    });
  }

  private hasMedia(msg: any): boolean {
    const m = msg.message;
    if (!m) return false;
    return !!(m.imageMessage || m.audioMessage || m.videoMessage || m.documentMessage || m.stickerMessage);
  }

  private async extractMedia(msg: any): Promise<{ base64: string; type: string; mimetype: string; filename: string } | null> {
    const m = msg.message;
    if (!m) return null;

    let type = '';
    let mimetype = '';
    let filename = '';

    if (m.imageMessage) {
      type = 'image';
      mimetype = m.imageMessage.mimetype || 'image/jpeg';
    } else if (m.audioMessage) {
      type = 'audio';
      mimetype = m.audioMessage.mimetype || 'audio/ogg; codecs=opus';
    } else if (m.videoMessage) {
      type = 'video';
      mimetype = m.videoMessage.mimetype || 'video/mp4';
    } else if (m.documentMessage) {
      type = 'document';
      mimetype = m.documentMessage.mimetype || 'application/octet-stream';
      filename = m.documentMessage.fileName || '';
    } else if (m.stickerMessage) {
      type = 'sticker';
      mimetype = m.stickerMessage.mimetype || 'image/webp';
    } else {
      return null;
    }

    try {
      const buffer = await downloadMediaMessage(msg, 'buffer', {}, {
        logger: pino({ level: 'silent' }) as any,
        reuploadRequest: this.sock.updateMediaMessage,
      });
      const base64 = Buffer.from(buffer as Buffer).toString('base64');
      return { base64, type, mimetype, filename };
    } catch (err) {
      console.error('Failed to download media:', err);
      return null;
    }
  }

  private extractMessageContent(msg: any): string | null {
    const message = msg.message;
    if (!message) return null;

    if (message.conversation) return message.conversation;
    if (message.extendedTextMessage?.text) return message.extendedTextMessage.text;
    if (message.imageMessage?.caption) return `[Image] ${message.imageMessage.caption}`;
    if (message.videoMessage?.caption) return `[Video] ${message.videoMessage.caption}`;
    if (message.documentMessage?.caption) return `[Document] ${message.documentMessage.caption}`;
    if (message.audioMessage) return `[Voice Message]`;
    if (message.imageMessage) return `[Image]`;
    if (message.videoMessage) return `[Video]`;
    if (message.documentMessage) return `[Document]`;
    if (message.stickerMessage) return `[Sticker]`;

    return null;
  }

  async sendMessage(to: string, text: string): Promise<any> {
    if (!this.sock) throw new Error('Not connected');
    return await this.sock.sendMessage(to, { text });
  }

  async sendMedia(to: string, base64Data: string, mimetype: string, filename: string): Promise<any> {
    if (!this.sock) throw new Error('Not connected');

    const buffer = Buffer.from(base64Data, 'base64');

    if (mimetype.startsWith('image/')) {
      return await this.sock.sendMessage(to, { image: buffer, mimetype, caption: '' });
    } else if (mimetype.startsWith('video/')) {
      return await this.sock.sendMessage(to, { video: buffer, mimetype, caption: '' });
    } else if (mimetype.startsWith('audio/')) {
      return await this.sock.sendMessage(to, { audio: buffer, mimetype, ptt: true });
    } else {
      return await this.sock.sendMessage(to, { document: buffer, mimetype, fileName: filename });
    }
  }

  async deleteMessage(remoteJid: string, msgId: string, fromMe: boolean, participant?: string): Promise<void> {
    if (!this.sock) throw new Error('Not connected');
    const key: any = { remoteJid, id: msgId, fromMe };
    if (participant) key.participant = participant;
    await this.sock.sendMessage(remoteJid, { delete: key });
  }

  async fetchMessageHistory(chatJid: string, count: number = 50): Promise<void> {
    if (!this.sock) throw new Error('Not connected');
    // Use Baileys chatModify to request on-demand history
    // Results arrive via messaging-history.set event
    try {
      await this.sock.fetchMessageHistory(count, { remoteJid: chatJid }, null);
      console.log(`Requested ${count} history messages for ${chatJid}`);
    } catch (err) {
      console.error('fetchMessageHistory failed:', err);
    }
  }

  async checkOnWhatsApp(phoneNumbers: string[]): Promise<Array<{jid: string; exists: boolean}>> {
    if (!this.sock) throw new Error('Not connected');
    return await this.sock.onWhatsApp(...phoneNumbers);
  }

  async addContact(jid: string, fullName: string, firstName?: string): Promise<void> {
    if (!this.sock) throw new Error('Not connected');
    const contact: any = { fullName };
    if (firstName) contact.firstName = firstName;
    contact.saveOnPrimaryAddressbook = true;
    await this.sock.addOrEditContact(jid, contact);
  }

  async removeContact(jid: string): Promise<void> {
    if (!this.sock) throw new Error('Not connected');
    await this.sock.removeContact(jid);
  }

  async sendPresenceUpdate(jid: string, type: 'composing' | 'paused' | 'available'): Promise<void> {
    if (!this.sock) throw new Error('Not connected');
    await this.sock.presenceSubscribe(jid);
    await this.sock.sendPresenceUpdate(type, jid);
  }

  async getProfilePicture(jid: string): Promise<string | null> {
    if (!this.sock) throw new Error('Not connected');
    try {
      return await this.sock.profilePictureUrl(jid, 'preview');
    } catch {
      return null;
    }
  }

  async disconnect(): Promise<void> {
    if (this.sock) {
      this.sock.end(undefined);
      this.sock = null;
    }
  }
}
