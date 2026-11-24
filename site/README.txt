Claude Taskbar website, ready for Cloudflare Pages

1. Copy the app's disk image into this folder:
     cp ~/Documents/Tracker/dist/Claude-Taskbar-1.1.0.dmg .
   (The download buttons point to that exact file name. If you release a new
   version, update the name in index.html too.)

2. Deploy this folder. Either:
   - Cloudflare dashboard: Workers & Pages > Create > Pages > Upload assets,
     name the project (for example claude-taskbar), and drag in this folder.
   - Or from Terminal:
       npx wrangler pages deploy . --project-name claude-taskbar

Your site will be at https://claude-taskbar.pages.dev (or the name you chose).
