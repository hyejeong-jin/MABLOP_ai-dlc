// Copyright, signature and contact line shown at the bottom of every screen.
import { CONTACT_EMAIL, DEVELOPER } from "../config";

export function SiteFooter() {
  const year = new Date().getFullYear();
  return (
    <footer className="site-footer">
      <span>
        © {year} MABLOP · Made with care by <strong>{DEVELOPER}</strong>
      </span>
      <span className="footer-sep" aria-hidden="true">
        ·
      </span>
      <span>
        contact : <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
      </span>
    </footer>
  );
}