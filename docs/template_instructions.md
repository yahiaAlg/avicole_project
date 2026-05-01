## How to Use This Prompt

1. Paste the full content of `DESIGN_SYSTEM_avicole.md` into the spec section above.
2. Paste the full content of `mini_spec_avicole.md` into the spec section above.
3. Send this prompt to Claude.
4. Claude will ask: **"Prêt. Veuillez coller les fichiers backend pour `base.html`
   (models, views, context processor, urls)."**
5. Paste the relevant backend files for that template.
6. Claude generates the complete template (A + B + C) and asks for the next page.
7. Continue through all pages in order.
8. After `accounts/settings.html`, Claude outputs a cross-template summary:
   broken URL references, missing context variables, inconsistent nav block
   usage, and any AP/dépense separation violations spotted across templates.

**Do not paste backend files for multiple pages at once.**
**Do not ask Claude to skip a page — later pages depend on base.html patterns.**

**End of Prompt**
