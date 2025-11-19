Plus30Chatbot Upgrade7 - Coin Economy
-------------------------------------
Files:
- main.py
- requirements.txt
- Procfile

Deploy:
- Upload to Render (ZIP) or push to GitHub and connect Render.
- Set BOT_TOKEN environment variable (or leave hardcoded inside file).
- Start service; bot uses polling by default.

Commands:
/start - show help
/profile set <gender> <age> - set profile
/find [gender] [age] - find partner
/leave - leave chat
/balance - show coin balance
/daily - claim daily reward
/fastmatch - spend 5 coins to jump queue or instant match
/ticket - buy lottery ticket (5 coins)
/topcoins - show leaderboard
/gift <user_id> <amount> - gift coins
/picklot - admin only, pick lottery winner (for testing)
