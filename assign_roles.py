import requests, json, time, os

TOKEN = os.environ['DISCORD_BOT_TOKEN']
GUILD_ID = os.environ['DISCORD_GUILD_ID']
headers = {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}
BASE = "https://discord.com/api/v10"

def req(method, path, data=None):
    r = requests.request(method, f"{BASE}{path}", headers=headers, json=data)
    if r.status_code == 429:
        retry = r.json().get('retry_after', 1)
        time.sleep(retry + 0.3)
        return req(method, path, data)
    return r

with open('/tmp/nexplay_roles.json') as f:
    role_ids = json.load(f)

member_role_id   = role_ids["🎮 NexPlay Member"]
newcomer_role_id = role_ids["🌱 Newcomer"]
bot_role_id      = role_ids["🤖 Bots"]
bot_self_id      = req("GET", "/users/@me").json()["id"]

# Fetch all members
print("📋 Fetching all members...", flush=True)
all_members = []
after = "0"
while True:
    r = req("GET", f"/guilds/{GUILD_ID}/members?limit=1000&after={after}")
    batch = r.json()
    if not isinstance(batch, list) or not batch:
        break
    all_members.extend(batch)
    after = batch[-1]['user']['id']
    print(f"  Fetched {len(all_members)}...", flush=True)
    if len(batch) < 1000:
        break
    time.sleep(0.3)

bots   = [m for m in all_members if m['user'].get('bot') and m['user']['id'] != bot_self_id]
humans = [m for m in all_members if not m['user'].get('bot') and m['user']['id'] != bot_self_id]
print(f"✅ {len(humans)} humans | {len(bots)} bots", flush=True)

# Assign NexPlay Member to all humans missing it
missing_humans = [m for m in humans if member_role_id not in m.get('roles', [])]
print(f"\n🎮 Assigning Member role to {len(missing_humans)} members...", flush=True)
m_done = 0
for m in missing_humans:
    uid = m['user']['id']
    r = req("PUT", f"/guilds/{GUILD_ID}/members/{uid}/roles/{member_role_id}")
    if r.status_code == 204:
        m_done += 1
    if m_done % 100 == 0 and m_done > 0:
        print(f"  ✅ {m_done}/{len(missing_humans)} assigned...", flush=True)
    time.sleep(0.08)
print(f"✅ Member role done: {m_done}", flush=True)

# Assign Bot role to all bots missing it
missing_bots = [m for m in bots if bot_role_id not in m.get('roles', [])]
print(f"\n🤖 Assigning Bot role to {len(missing_bots)} bots...", flush=True)
b_done = 0
for m in missing_bots:
    uid = m['user']['id']
    r = req("PUT", f"/guilds/{GUILD_ID}/members/{uid}/roles/{bot_role_id}")
    if r.status_code == 204:
        b_done += 1
        print(f"  ✅ Bot: {m['user']['username']}", flush=True)
    time.sleep(0.15)
print(f"✅ Bot role done: {b_done}", flush=True)

# Assign Newcomer to roleless humans
roleless = [m for m in humans if len(m.get('roles', [])) == 0]
print(f"\n🌱 Assigning Newcomer to {len(roleless)} roleless members...", flush=True)
n_done = 0
for m in roleless:
    uid = m['user']['id']
    r = req("PUT", f"/guilds/{GUILD_ID}/members/{uid}/roles/{newcomer_role_id}")
    if r.status_code == 204:
        n_done += 1
    time.sleep(0.08)
print(f"✅ Newcomer done: {n_done}", flush=True)

print(f"\n🎉 ALL DONE!", flush=True)
print(f"  🎮 NexPlay Member assigned: {m_done}", flush=True)
print(f"  🤖 Bots assigned: {b_done}", flush=True)
print(f"  🌱 Newcomer assigned: {n_done}", flush=True)

# Write completion flag
with open('/tmp/roles_done.txt', 'w') as f:
    f.write(f"DONE\nMember:{m_done}\nBots:{b_done}\nNewcomer:{n_done}\n")
print("✅ Completion flag written.", flush=True)
