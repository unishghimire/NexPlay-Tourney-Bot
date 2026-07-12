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

function buildImageUrl(image_type: string, params: any): string {
  const { tournament_name, game, prize_pool, date, extra_data = {} } = params;
  let prompt = '';
  switch (image_type) {
    case 'poster':
      prompt = `Professional esports tournament poster "${tournament_name}", ${game} gaming, prize pool ${prize_pool}, date ${date}, neon dark background, gold purple, cinematic high quality art`;
      break;
    case 'roadmap':
      prompt = `Tournament roadmap timeline "${tournament_name}" ${game} esports, stages: Registration, Group Draw, Schedule, Match Day, Results, Champion, modern dark design`;
      break;
    case 'group_draw':
      prompt = `Esports group draw card "${tournament_name}" ${game}, ${extra_data.groups_text || 'Groups A B C D'}, dark neon design, colorful panels`;
      break;
    case 'match_schedule':
      prompt = `Match schedule card "${tournament_name}" ${game}, ${extra_data.schedule_text || 'match schedule'}, dark professional infographic`;
      break;
    case 'results_card':
      prompt = `Match result card ${extra_data.player1} vs ${extra_data.player2}, score ${extra_data.score}, winner ${extra_data.winner}, "${tournament_name}" dark dramatic victory graphic`;
      break;
    case 'champion_card':
      prompt = `Champion victory card "${extra_data.winner}" wins "${tournament_name}" ${game}, golden trophy, confetti, epic cinematic design`;
      break;
    default:
      prompt = `Professional esports graphic "${tournament_name}" ${game}`;
  }
  return `https://image.pollinations.ai/prompt/${encodeURIComponent(prompt)}?width=1280&height=640&nologo=true&seed=${Date.now()}&model=flux`;
}

Deno.serve(async (req) => {
  const base44 = createClientFromRequest(req);
  let body: any = {};
  try { body = await req.json(); } catch {}

  const { command, guild_id, tournament_name, game, prize_pool, date, format,
    max_players, description, channel_ids, player1, player2, score, winner, second, third } = body;

  const ok = (data: any) => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
  const err = (msg: string, status = 400) => new Response(JSON.stringify({ error: msg }), { status, headers: { 'Content-Type': 'application/json' } });

  try {
    // Validate server
    const servers = await base44.asServiceRole.entities.Server.filter({ guild_id });
    if (!servers?.length) return err('Server not registered with NexPlay.', 403);
    const server = servers[0];
    if (server.subscription_status === 'banned') return err('🚫 This server is banned from NexPlay.', 403);
    if (server.subscription_status === 'expired') return err('⏰ Your subscription has expired. Please renew.', 403);

    const ch = {
      announcements: channel_ids?.announcements || server.announcement_channel_id,
      registration: channel_ids?.registration || server.registration_channel_id,
      brackets: channel_ids?.brackets || server.brackets_channel_id,
      champions: channel_ids?.champions || server.champions_channel_id,
    };

    switch (command) {

      case 'create_tournament': {
        if ((server.tournaments_used_this_cycle || 0) >= (server.tournaments_limit || 2))
          return err(`📊 Plan limit of ${server.tournaments_limit} tournaments reached. Please upgrade.`, 403);

        const poster_url = buildImageUrl('poster', { tournament_name, game, prize_pool, date });
        const roadmap_url = buildImageUrl('roadmap', { tournament_name, game });

        const tournament = await base44.asServiceRole.entities.Tournament.create({
          server_id: server.id, guild_id, name: tournament_name, game,
          format: format || 'single_elim', prize_pool, description,
          status: 'registration_open', max_players: max_players || 16, registered_count: 0,
          tournament_date: date, poster_image_url: poster_url, roadmap_image_url: roadmap_url,
          announcement_channel_id: ch.announcements, registration_channel_id: ch.registration,
          brackets_channel_id: ch.brackets, champions_channel_id: ch.champions
        });

        // Log images
        for (const [type, url] of [['poster', poster_url], ['roadmap', roadmap_url]]) {
          await base44.asServiceRole.entities.ImageGeneration.create({
            tournament_id: tournament.id, guild_id, image_type: type,
            pollinations_url: url, generated_at: new Date().toISOString()
          });
        }

        // Announcement
        const announceMsg = await discordPost(ch.announcements, { embeds: [{
          title: `🏆 ${tournament_name} — TOURNAMENT ANNOUNCED!`,
          description: `**🎮 Game:** ${game}\n**📋 Format:** ${format || 'Single Elimination'}\n**🏅 Prize Pool:** ${prize_pool}\n**📅 Date:** ${date}\n**👥 Max Players:** ${max_players || 16}\n\n${description ? `> ${description}\n\n` : ''}**📍 Roadmap:**\n✅ Registration Open → 🎯 Group Draw → 📅 Schedule → ⚔️ Match Day → 📊 Results → 🏆 Champion`,
          color: 0xFFD700, image: { url: poster_url },
          footer: { text: '🇳🇵 NexPlay Tournament System' }, timestamp: new Date().toISOString()
        }]});

        // Registration post
        await discordPost(ch.registration, { embeds: [{
          title: `📋 Registration OPEN — ${tournament_name}`,
          description: `**Registration is now OPEN!** 🎉\n\n**🎮 Game:** ${game}\n**🏅 Prize Pool:** ${prize_pool}\n**👥 Slots:** ${max_players || 16} players max\n**📅 Date:** ${date}\n\n**How to Register:**\n> Type your **in-game name** in this channel!\n> Example: \`YourGameID#1234\`\n\n*Limited spots — register now!*`,
          color: 0x00FF7F, image: { url: roadmap_url },
          footer: { text: '🇳🇵 NexPlay Tournament System' }, timestamp: new Date().toISOString()
        }]});

        await base44.asServiceRole.entities.Server.update(server.id, {
          tournaments_used_this_cycle: (server.tournaments_used_this_cycle || 0) + 1,
          last_active_at: new Date().toISOString()
        });

        await base44.asServiceRole.entities.AnnouncementLog.create({
          tournament_id: tournament.id, guild_id, milestone: 'tournament_created',
          channel_id: ch.announcements, message_id: announceMsg?.id,
          announced_at: new Date().toISOString(),
          content_summary: `Tournament "${tournament_name}" created`
        });

        return ok({ success: true, tournament_id: tournament.id, poster_url, roadmap_url, message: `✅ "${tournament_name}" announced! Poster + roadmap published.` });
      }

      case 'close_registration': {
        const ts = await base44.asServiceRole.entities.Tournament.filter({ guild_id, name: tournament_name });
        if (!ts?.length) return err(`Tournament "${tournament_name}" not found.`, 404);
        const t = ts[0];
        await base44.asServiceRole.entities.Tournament.update(t.id, { status: 'registration_closed' });
        const regs = await base44.asServiceRole.entities.Registration.filter({ tournament_id: t.id });
        const playerList = regs.slice(0, 30).map((r: any, i: number) => `${i + 1}. **${r.player_username}**`).join('\n') || '*None registered*';
        const msg = await discordPost(t.announcement_channel_id || ch.announcements, { embeds: [{
          title: `🔒 Registration CLOSED — ${tournament_name}`,
          description: `Registration closed with **${regs.length} players** confirmed!\n\n**Players:**\n${playerList}\n\nGroups and schedule coming soon! 👀`,
          color: 0xFF4500, footer: { text: '🇳🇵 NexPlay Tournament System' }, timestamp: new Date().toISOString()
        }]});
        await base44.asServiceRole.entities.AnnouncementLog.create({
          tournament_id: t.id, guild_id, milestone: 'registration_closed',
          channel_id: ch.announcements, message_id: msg?.id,
          announced_at: new Date().toISOString(), content_summary: `Registration closed: ${regs.length} players`
        });
        return ok({ success: true, player_count: regs.length });
      }

      case 'generate_groups': {
        const ts = await base44.asServiceRole.entities.Tournament.filter({ guild_id, name: tournament_name });
        if (!ts?.length) return err(`Tournament "${tournament_name}" not found.`, 404);
        const t = ts[0];
        const regs = await base44.asServiceRole.entities.Registration.filter({ tournament_id: t.id });
        if (regs.length < 2) return err('Need at least 2 players to generate groups.');
        const shuffled = [...regs].sort(() => Math.random() - 0.5);
        const groupSize = Math.max(2, Math.ceil(shuffled.length / Math.ceil(shuffled.length / 4)));
        const groupLabels = ['A','B','C','D','E','F','G','H'];
        const groups: any[] = [];
        for (let i = 0; i < shuffled.length; i += groupSize) {
          const gPlayers = shuffled.slice(i, i + groupSize);
          const label = groupLabels[groups.length] || `G${groups.length+1}`;
          await base44.asServiceRole.entities.TournamentGroup.create({
            tournament_id: t.id, guild_id, group_label: label,
            player_ids: gPlayers.map((p: any) => p.id),
            player_names: gPlayers.map((p: any) => p.player_username),
            generated_at: new Date().toISOString()
          });
          for (const p of gPlayers) await base44.asServiceRole.entities.Registration.update(p.id, { group_label: label });
          groups.push({ label, players: gPlayers.map((p: any) => p.player_username) });
        }
        const groups_text = groups.map(g => `Group ${g.label}: ${g.players.join(', ')}`).join(' | ');
        const group_image_url = buildImageUrl('group_draw', { tournament_name, game: t.game, extra_data: { groups_text } });
        await base44.asServiceRole.entities.Tournament.update(t.id, { status: 'groups_generated' });
        await base44.asServiceRole.entities.ImageGeneration.create({
          tournament_id: t.id, guild_id, image_type: 'group_draw',
          pollinations_url: group_image_url, generated_at: new Date().toISOString()
        });
        const groupDesc = groups.map(g => `**Group ${g.label}:**\n${g.players.map((p: string) => `> 🎮 ${p}`).join('\n')}`).join('\n\n');
        const bMsg = await discordPost(t.brackets_channel_id || ch.brackets, { embeds: [{
          title: `🎯 Group Draw — ${tournament_name}`, description: groupDesc,
          color: 0x9B59B6, image: { url: group_image_url },
          footer: { text: '🇳🇵 NexPlay Tournament System' }, timestamp: new Date().toISOString()
        }]});
        await discordPost(t.announcement_channel_id || ch.announcements, { embeds: [{
          title: `📢 Groups Revealed — ${tournament_name}!`,
          description: `The group draw is complete! 🎉\nCheck <#${t.brackets_channel_id || ch.brackets}> to see your group!\n\n⚔️ **${regs.length} players** | **${groups.length} groups** — may the best win!`,
          color: 0x9B59B6, footer: { text: '🇳🇵 NexPlay Tournament System' }
        }]});
        await base44.asServiceRole.entities.AnnouncementLog.create({
          tournament_id: t.id, guild_id, milestone: 'groups_revealed',
          channel_id: ch.brackets, message_id: bMsg?.id,
          announced_at: new Date().toISOString(), content_summary: `${groups.length} groups generated`
        });
        return ok({ success: true, groups, group_image_url });
      }

      case 'post_schedule': {
        const ts = await base44.asServiceRole.entities.Tournament.filter({ guild_id, name: tournament_name });
        if (!ts?.length) return err(`Tournament "${tournament_name}" not found.`, 404);
        const t = ts[0];
        const schedule_text = body.schedule_text || 'Matches scheduled — check brackets channel';
        const schedule_image_url = buildImageUrl('match_schedule', { tournament_name, game: t.game, extra_data: { schedule_text } });
        await base44.asServiceRole.entities.Tournament.update(t.id, { status: 'scheduled', schedule_image_url });
        await base44.asServiceRole.entities.ImageGeneration.create({
          tournament_id: t.id, guild_id, image_type: 'match_schedule',
          pollinations_url: schedule_image_url, generated_at: new Date().toISOString()
        });
        const msg = await discordPost(t.brackets_channel_id || ch.brackets, { embeds: [{
          title: `📅 Match Schedule — ${tournament_name}`,
          description: `**The match schedule is LIVE!** ⚔️\n\n${schedule_text}\n\n⚠️ Be ready 10 min before your match — late arrivals forfeit!`,
          color: 0x1E90FF, image: { url: schedule_image_url },
          footer: { text: '🇳🇵 NexPlay Tournament System' }, timestamp: new Date().toISOString()
        }]});
        await discordPost(t.announcement_channel_id || ch.announcements, { embeds: [{
          title: `📢 Schedule Posted — ${tournament_name}`,
          description: `Match schedule is live in <#${t.brackets_channel_id || ch.brackets}>!\nCheck your match time. Good luck to all! 🎮`,
          color: 0x1E90FF, footer: { text: '🇳🇵 NexPlay Tournament System' }
        }]});
        await base44.asServiceRole.entities.AnnouncementLog.create({
          tournament_id: t.id, guild_id, milestone: 'schedule_posted',
          channel_id: ch.brackets, message_id: msg?.id,
          announced_at: new Date().toISOString(), content_summary: `Schedule posted`
        });
        return ok({ success: true, schedule_image_url });
      }

      case 'post_result': {
        const ts = await base44.asServiceRole.entities.Tournament.filter({ guild_id, name: tournament_name });
        if (!ts?.length) return err(`Tournament "${tournament_name}" not found.`, 404);
        const t = ts[0];
        const results_image_url = buildImageUrl('results_card', { tournament_name, game: t.game, extra_data: { player1, player2, score, winner } });
        await base44.asServiceRole.entities.Match.create({
          tournament_id: t.id, guild_id,
          round_number: body.round_number || 1, match_number: body.match_number || 1,
          player1_username: player1, player2_username: player2,
          winner_username: winner, status: 'completed',
          results_card_image_url: results_image_url
        });
        await base44.asServiceRole.entities.ImageGeneration.create({
          tournament_id: t.id, guild_id, image_type: 'results_card',
          pollinations_url: results_image_url, generated_at: new Date().toISOString()
        });
        const msg = await discordPost(t.brackets_channel_id || ch.brackets, { embeds: [{
          title: `⚔️ Match Result — ${tournament_name}`,
          description: `**${player1}** vs **${player2}**\n\n🏆 **Winner: ${winner}**\n📊 **Score:** ${score || 'N/A'}\n\n> ${winner} advances to the next round!`,
          color: 0x1E90FF, image: { url: results_image_url },
          footer: { text: '🇳🇵 NexPlay Tournament System' }, timestamp: new Date().toISOString()
        }]});
        await base44.asServiceRole.entities.AnnouncementLog.create({
          tournament_id: t.id, guild_id, milestone: 'results_posted',
          channel_id: ch.brackets, message_id: msg?.id,
          announced_at: new Date().toISOString(), content_summary: `${player1} vs ${player2} — Winner: ${winner}`
        });
        return ok({ success: true, results_image_url, message: `${winner} wins!` });
      }

      case 'complete_tournament': {
        const ts = await base44.asServiceRole.entities.Tournament.filter({ guild_id, name: tournament_name });
        if (!ts?.length) return err(`Tournament "${tournament_name}" not found.`, 404);
        const t = ts[0];
        const champion_image_url = buildImageUrl('champion_card', { tournament_name, game: t.game, extra_data: { winner } });
        await base44.asServiceRole.entities.Tournament.update(t.id, {
          status: 'completed', winner_username: winner,
          second_place: second || '', third_place: third || '',
          completed_at: new Date().toISOString()
        });
        await base44.asServiceRole.entities.ImageGeneration.create({
          tournament_id: t.id, guild_id, image_type: 'champion_card',
          pollinations_url: champion_image_url, generated_at: new Date().toISOString()
        });
        await discordPost(t.champions_channel_id || ch.champions, { embeds: [{
          title: `🏆 CHAMPION — ${tournament_name}`,
          description: `**Congratulations to ${winner}!** 🎉🎊\n\n🥇 **1st Place:** ${winner}\n${second ? `🥈 **2nd Place:** ${second}\n` : ''}${third ? `🥉 **3rd Place:** ${third}\n` : ''}\n**🏅 Prize Pool:** ${t.prize_pool}\n\n*Thank you to all participants for an amazing tournament!*`,
          color: 0xFFD700, image: { url: champion_image_url },
          footer: { text: '🇳🇵 NexPlay Tournament System' }, timestamp: new Date().toISOString()
        }]});
        await discordPost(t.announcement_channel_id || ch.announcements, { embeds: [{
          title: `🎊 ${tournament_name} IS COMPLETE!`,
          description: `🥇 **Champion: ${winner}**\n${second ? `🥈 Runner-up: ${second}\n` : ''}${third ? `🥉 3rd: ${third}\n` : ''}\n**Prize:** ${t.prize_pool}\n\nSee you at the next tournament! 🎮`,
          color: 0xFFD700, footer: { text: '🇳🇵 NexPlay Tournament System' }, timestamp: new Date().toISOString()
        }]});
        await base44.asServiceRole.entities.AnnouncementLog.create({
          tournament_id: t.id, guild_id, milestone: 'tournament_complete',
          channel_id: ch.announcements, announced_at: new Date().toISOString(),
          content_summary: `Champion: ${winner}`
        });
        return ok({ success: true, champion_image_url, winner });
      }

      case 'announce': {
        const msg = await discordPost(body.target_channel || ch.announcements, { embeds: [{
          title: `📢 NexPlay Announcement`,
          description: body.message,
          color: body.color || 0xFFD700,
          footer: { text: '🇳🇵 NexPlay Tournament System' }, timestamp: new Date().toISOString()
        }]});
        return ok({ success: true, message_id: msg?.id });
      }

      case 'register_player': {
        const ts = await base44.asServiceRole.entities.Tournament.filter({ guild_id, name: tournament_name });
        if (!ts?.length) return err(`Tournament "${tournament_name}" not found.`, 404);
        const t = ts[0];
        if (t.status !== 'registration_open') return err('Registration is not open.');
        const existing = await base44.asServiceRole.entities.Registration.filter({ tournament_id: t.id, player_discord_id: body.player_discord_id });
        if (existing?.length) return err(`Already registered!`);
        const currentRegs = await base44.asServiceRole.entities.Registration.filter({ tournament_id: t.id });
        if (currentRegs.length >= (t.max_players || 16)) return err('Tournament is full!');
        const reg = await base44.asServiceRole.entities.Registration.create({
          tournament_id: t.id, guild_id, player_discord_id: body.player_discord_id,
          player_username: body.player_username, player_display_name: body.player_display_name,
          team_name: body.team_name, registered_at: new Date().toISOString(),
          checked_in: false, seed_number: currentRegs.length + 1
        });
        await base44.asServiceRole.entities.Tournament.update(t.id, { registered_count: currentRegs.length + 1 });
        return ok({ success: true, registration_id: reg.id, message: `✅ ${body.player_username} registered! (${currentRegs.length+1}/${t.max_players})` });
      }

      case 'get_status': {
        const ts = await base44.asServiceRole.entities.Tournament.filter({ guild_id });
        return ok({ success: true, tournaments: ts });
      }

      default:
        return err(`Unknown command: ${command}`);
    }
  } catch (error: any) {
    console.error('Error:', error);
    return new Response(JSON.stringify({ error: error.message }), { status: 500, headers: { 'Content-Type': 'application/json' } });
  }
});
