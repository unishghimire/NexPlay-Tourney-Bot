import { createClientFromRequest } from 'npm:@base44/sdk@0.8.31';

const DISCORD_API = 'https://discord.com/api/v10';
const BOT_TOKEN = Deno.env.get('DISCORD_BOT_TOKEN');

async function discordPost(channelId: string, content: any) {
  if (!channelId) return null;
  try {
    const r = await fetch(`${DISCORD_API}/channels/${channelId}/messages`, {
      method: 'POST',
      headers: { 'Authorization': `Bot ${BOT_TOKEN}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(content)
    });
    return r.ok ? r.json() : null;
  } catch { return null; }
}

Deno.serve(async (req) => {
  const base44 = createClientFromRequest(req);
  let body: any = {};
  try { body = await req.json(); } catch {}

  const { guild_id, channel_id, user_discord_id, user_username = 'Player', question = '', tournament_id } = body;
  const lowerQ = question.toLowerCase();

  const ok = (data: any) => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });

  const has = (keywords: string[]) => keywords.some(k => lowerQ.includes(k));

  const isBilling = has(['payment','refund','billing','charge','subscription','buy','purchase','cost','fee','upgrade','plan','price']);
  const isAccount = has(['account','banned','suspended','login','password','hack','stolen']);
  const isRegistration = has(['register','registration','sign up','join tournament','how to join','enroll','how do i']);
  const isSchedule = has(['when','schedule','time','date','what time','match time','start']);
  const isPrize = has(['prize','reward','win','winning','cash','money reward']);
  const isRules = has(['rules','rule','allowed','cheat','hack','exploit','regulation','fair']);
  const isBracket = has(['bracket','group','draw','round','matchup','opponent','who do i play','my group']);
  const isResult = has(['result','score','winner','who won','standing','leaderboard']);

  let ai_response = '';
  let confidence_score = 0.9;
  let routed_to_human = false;
  let category = 'general';

  let activeTournament: any = null;
  try {
    const ts = await base44.asServiceRole.entities.Tournament.filter({ guild_id });
    activeTournament = ts?.find((t: any) => !['completed','cancelled'].includes(t.status));
  } catch {}

  const tName = activeTournament?.name || 'the current tournament';
  const tGame = activeTournament?.game || '';
  const tStatus = activeTournament?.status || '';

  if (isBilling) {
    category = 'billing'; routed_to_human = true; confidence_score = 1.0;
    ai_response = `💳 This involves **billing or payments**. I've notified the admin team!\n\nA staff member will respond shortly. For urgent matters, please contact **@Unish** directly.`;
    try {
      await base44.asServiceRole.entities.AdminNotification.create({
        type: 'escalation', server_id: guild_id, guild_name: guild_id,
        message: `💳 Billing question from **${user_username}**: "${question}"`,
        severity: 'warning', read_by_unish: false
      });
    } catch {}
  } else if (isAccount) {
    category = 'account'; routed_to_human = true; confidence_score = 1.0;
    ai_response = `🔐 Account issues need a **human moderator**. I've flagged this for the team!\nA staff member will assist you shortly.`;
  } else if (isRegistration) {
    category = 'tournament';
    if (activeTournament) {
      ai_response = tStatus === 'registration_open'
        ? `📋 **How to Register for ${tName}:**\n1. Go to the **registration channel**\n2. Type your **in-game name/ID**\n3. Wait for confirmation! ✅\n\n**Status:** Registration **OPEN** 🟢\n**Game:** ${tGame} | **Prize:** ${activeTournament.prize_pool}`
        : `📋 Registration for **${tName}** is currently **CLOSED** 🔒\nStatus: \`${tStatus}\`\n\nWatch **announcements** for the next tournament!`;
    } else {
      ai_response = `📋 No active registration right now. Watch **announcements** for upcoming tournaments! 🎮`;
    }
  } else if (isSchedule) {
    category = 'tournament';
    ai_response = activeTournament
      ? `📅 **${tName} Schedule:**\nStatus: \`${tStatus}\` | Game: ${tGame} | Date: ${activeTournament.tournament_date || 'TBA'}\n\nCheck the **brackets channel** for the full match schedule!`
      : `📅 No active tournaments right now. Watch **announcements** for upcoming events!`;
  } else if (isPrize) {
    category = 'tournament';
    ai_response = activeTournament
      ? `🏆 **Prize Pool — ${tName}:**\n💰 **${activeTournament.prize_pool}**\n\nGive it your all and become the champion! 🔥`
      : `🏆 Prize pool details are announced with each tournament. Watch **announcements**!`;
  } else if (isRules) {
    category = 'tournament';
    ai_response = `📜 **Tournament Rules (Summary):**\n\n**1.** No cheating, hacking, or exploiting — instant DQ\n**2.** Be in voice channel **10 min before** your match\n**3.** Screenshot results and post them\n**4.** Respect all opponents and hosts\n**5.** Host decisions are **final**\n**6.** No-show = forfeit after 5 minutes\n\nFull rules in the **#tournament-rules** channel!`;
  } else if (isBracket) {
    category = 'tournament';
    ai_response = activeTournament && ['groups_generated','scheduled','in_progress'].includes(tStatus)
      ? `🎯 **Brackets for ${tName}:**\nCheck the **brackets channel** for your group and matchups!\nStatus: \`${tStatus}\` — matches ${tStatus === 'in_progress' ? 'are LIVE! 🔥' : 'coming soon!'}`
      : `🎯 Brackets haven't been generated yet. They'll appear in the **brackets channel** after registration closes!`;
  } else if (isResult) {
    category = 'tournament';
    ai_response = `📊 Match results are posted in the **brackets channel** after each match.\n${activeTournament ? `Current: **${tName}** (${tStatus})` : 'No active tournament right now.'}\n\nCheck the brackets channel for latest results!`;
  } else {
    category = 'general'; confidence_score = 0.4; routed_to_human = true;
    ai_response = `🤖 I'm not sure about that one! I've flagged this for a **human moderator**.\n\nA staff member will respond soon. Quick help:\n• **Registration** → registration channel\n• **Schedule** → brackets channel\n• **Rules** → tournament-rules channel`;
    try {
      await base44.asServiceRole.entities.AdminNotification.create({
        type: 'escalation', server_id: guild_id, guild_name: guild_id,
        message: `❓ Unhandled support Q from **${user_username}**: "${question}"`,
        severity: 'info', read_by_unish: false
      });
    } catch {}
  }

  try {
    await base44.asServiceRole.entities.SupportMessage.create({
      guild_id, tournament_id: tournament_id || activeTournament?.id,
      user_discord_id, user_username, question, ai_response, confidence_score, routed_to_human, category
    });
  } catch {}

  if (channel_id) {
    await discordPost(channel_id, { embeds: [{
      description: `**${user_username} asked:** ${question}\n\n${ai_response}`,
      color: routed_to_human ? 0xFF9900 : 0x00FF7F,
      footer: { text: routed_to_human ? '🇳🇵 NexPlay Support — Routed to Staff' : '🇳🇵 NexPlay AI Support' }
    }]});
  }

  return ok({ success: true, response: ai_response, routed_to_human, category, confidence_score });
});
