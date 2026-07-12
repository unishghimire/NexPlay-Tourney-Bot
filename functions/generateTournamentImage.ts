import { createClientFromRequest } from 'npm:@base44/sdk@0.8.31';

Deno.serve(async (req) => {
  const base44 = createClientFromRequest(req);

  let body: any = {};
  try { body = await req.json(); } catch {}

  const {
    tournament_id,
    guild_id,
    tournament_name = 'Tournament',
    game = 'Game',
    prize_pool = 'TBA',
    date = 'TBA',
    image_type = 'poster',
    extra_data = {}
  } = body;

  let prompt = '';

  switch (image_type) {
    case 'poster':
      prompt = `Professional esports tournament poster, "${tournament_name}", ${game} gaming tournament, prize pool ${prize_pool}, date ${date}, dramatic neon lighting, dark background, gold and purple color scheme, epic gaming atmosphere, cinematic high quality digital art`;
      break;
    case 'roadmap':
      prompt = `Tournament roadmap timeline infographic "${tournament_name}" ${game} esports, stages: 1.Registration Open 2.Group Draw 3.Match Schedule 4.Match Day 5.Results 6.Champion Crowned, modern dark clean design, colorful milestone icons, professional esports graphic`;
      break;
    case 'group_draw':
      const groupsText = extra_data.groups_text || 'Groups A B C D';
      prompt = `Esports tournament group draw reveal card "${tournament_name}" ${game}, ${groupsText}, dramatic dark reveal design, colorful group panels, neon accents, professional bracket graphic`;
      break;
    case 'match_schedule':
      const schedText = extra_data.schedule_text || 'Multiple rounds and matches';
      prompt = `Tournament match schedule card "${tournament_name}" ${game} esports, ${schedText}, clean dark modern table design, professional infographic, time slots and matchups displayed, esports aesthetic`;
      break;
    case 'results_card':
      const rp1 = extra_data.player1 || 'Player 1';
      const rp2 = extra_data.player2 || 'Player 2';
      const rscore = extra_data.score || 'vs';
      const rwinner = extra_data.winner || rp1;
      prompt = `Esports match result card, "${rp1}" vs "${rp2}", score ${rscore}, winner "${rwinner}" golden glow highlight, "${tournament_name}" ${game} tournament, dark dramatic victory graphic, confetti`;
      break;
    case 'champion_card':
      const champ = extra_data.winner || 'Champion';
      prompt = `Champion victory card "${champ}" wins "${tournament_name}" ${game} esports championship, golden trophy, confetti explosion, champion crown, fireworks, epic cinematic dark design with gold purple lights`;
      break;
    default:
      prompt = `Professional esports gaming graphic "${tournament_name}", ${game} tournament, dark neon aesthetic`;
  }

  try {
    const encodedPrompt = encodeURIComponent(prompt);
    const seed = Date.now();
    const image_url = `https://image.pollinations.ai/prompt/${encodedPrompt}?width=1280&height=640&nologo=true&seed=${seed}&model=flux`;

    if (tournament_id) {
      await base44.asServiceRole.entities.ImageGeneration.create({
        tournament_id,
        guild_id: guild_id || '',
        image_type,
        prompt,
        pollinations_url: image_url,
        generated_at: new Date().toISOString()
      });
    }

    return new Response(JSON.stringify({ success: true, image_url, prompt, image_type }), {
      headers: { 'Content-Type': 'application/json' }
    });
  } catch (error: any) {
    return new Response(JSON.stringify({ success: false, error: error.message }), {
      status: 500, headers: { 'Content-Type': 'application/json' }
    });
  }
});
