<!-- build-plan:begin -->
## Active build plan — nexplay_tournament_system
Work through every step, and confirm each is satisfied before telling the user the agent is ready.

- [ ] 1. Create the nexplay_tournament_system agent with the persona, mission, and operating rules defined above.
- [ ] 2. Write operating rules to .agents/rules/nexplay_tournament_system.md — these cover multi-tenant isolation, plan limit enforcement, destructive action confirmation, Pollinations.ai usage, support routing, API error handling, and admin-only access controls.
- [ ] 3. Create all entities: Server, Tournament, Registration, Group, Match, Plan, Subscription, PromoCode, Offer, ImageGeneration, AnnouncementLog, SupportMessage, AdminNotification. Seed the Plan entity with Free Trial, Basic, and Pro default plans.
- [ ] 4. Create the generate-image backend function that calls Pollinations.ai (https://image.pollinations.ai/prompt/{encoded_prompt}) and returns the image URL. This function is used by all image-generation skills.
- [ ] 5. Create the Discord bot connector using the bot token from sandbox secrets. Authorize the bot to read messages, send messages, and manage messages in the designated tournament channels across customer servers.
- [ ] 6. Set up the Discord command handler automation: incoming command → parse → execute corresponding skill → post result in originating channel.
- [ ] 7. Set up the Discord support-channel automation: new message in support-ticket → classify → answer or route to human → log SupportMessage.
- [ ] 8. Build the run-full-tournament skill: create Tournament → generate poster → post to announcements → open registration → generate roadmap → post to registration channel.
- [ ] 9. Build the lifecycle-advance skill with all milestone handlers: registration_open, groups_revealed, match_starting, results_posted, tournament_complete.
- [ ] 10. Build the generate-groups, generate-schedule, post-match-result, and complete-tournament capabilities as backend functions that update entities and trigger lifecycle-advance for each milestone.
- [ ] 11. Build the answer-support-question skill with question classification and confidence scoring, routing billing/account questions to human and notifying Unish via AdminNotification.
- [ ] 12. Set up entity-change automations: Tournament status change → lifecycle-advance; Match completed → results card + bracket advancement check; Tournament completed → champion card + hall-of-champions post.
- [ ] 13. Set up the trial-expiry automation: schedule check on trial_expires_at → expire subscription → notify server owner → AdminNotification for Unish.
- [ ] 14. Set up the daily revenue-report cron automation (9am) → aggregate MRR, churn, signups, plan distribution → update dashboard cache.
- [ ] 15. Connect Stripe (read-only webhook) if Unish has a Stripe account — listen for subscription cancelled/updated events → sync Subscription entities. Skip if not yet available.
- [ ] 16. Build the admin panel web app pages: Dashboard (revenue, servers, tournaments, usage), Server Management (list + detail view), Subscription Management (plans CRUD), Promo Codes (CRUD), Offers (CRUD), Revenue Analytics (charts). All pages gated to Unish as the sole admin.
- [ ] 17. Build the admin-server-management skill: load server detail with full history, present actions (add trial, upgrade, downgrade, ban).
- [ ] 18. Build the admin-revenue-report skill: aggregate subscriptions, calculate MRR/churn/signups, return structured data for dashboard charts.
- [ ] 19. Build the ban-server capability: set Server subscription_status to banned, log reason, bot responds only with 'server banned' notice in that Discord server.
- [ ] 20. Build the promo-code and offer management capabilities: CRUD for PromoCode and Offer entities, with the offer-created automation posting to NEXPLAY ORG if targeted.
- [ ] 21. Test end-to-end in the NEXPLAY ORG server: create a test tournament, run the full lifecycle, verify all images generate via Pollinations.ai, all announcements post to the correct channels, and the admin panel reflects the activity.
- [ ] 22. Test multi-tenancy: simulate a second Discord server adding the bot, create a separate tournament, verify data isolation and plan limit enforcement.
- [ ] 23. Deploy the bot to be publicly addable to other Discord servers with an OAuth invite link and onboarding flow.
<!-- build-plan:end -->