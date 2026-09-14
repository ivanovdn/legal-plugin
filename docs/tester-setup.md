# Legal Triage — installing the assistant in Word

**Who this is for:** the legal team. This walks you through putting the Legal
Triage assistant into Word on your own laptop, one step at a time. You don't need
to understand any of the technology to follow it.

**How long:** about 15 minutes, once. After that the assistant is simply there
in Word.

**Desktop Word only.** Word in a browser (Word Online, or opening a document from
SharePoint in the browser) can't run it yet — see *Questions we get asked* at the
end.

**If anything doesn't match what you see below, stop and send us a screenshot.**
Nothing here is urgent enough to guess at.

---

## What you need before you start

| | |
|---|---|
| **The Word desktop app** | On Windows or on a Mac. Not Word in a browser. |
| **Permission to change two settings on your laptop** | On **Windows** that means local administrator rights. On a **Mac** it means your login password. If you're on Windows without admin rights, stop here and tell us — we'll get IT to do it a different way, and you don't need to chase it yourself. |
| **The office network or the VPN** | The assistant runs on an internal server. From outside the network it's invisible, and the panel simply won't load. |
| **The files we sent you** | `caddy-root-ca.crt` and `manifest.prod.xml`. Put both in your **Downloads** folder and leave them there until you've finished. |

### Which half of this document to follow

- **On a Mac** → [Part A](#part-a--installing-on-a-mac).
- **On Windows** → [Part B](#part-b--installing-on-windows).

Then **everybody** does [Step 6](#step-6-everyone--put-your-name-in) and reads
*What you'll see in the panel* onwards.

---

## Why two of the steps are fiddly (worth 30 seconds of your time)

The assistant's panel is a small web page that Word loads from our internal
server. Word refuses to load a web page unless your laptop already trusts the
server's security certificate — and ours is currently a certificate we issued
ourselves, because the company's IT-issued one hasn't arrived yet.

So one step tells your laptop **where** our server is, and another tells it to
**trust that one server's certificate**. That's the whole reason those two steps
exist, and they're the two that need permission.

**Both disappear once IT issues the proper certificate.** At that point installing
is a single step. If you'd rather wait for that than do these now, tell us — you
lose nothing by waiting.

---

# Part A — Installing on a Mac

Three of these steps use the **Terminal** app. Nothing needs to be typed out — you
paste a line in and press **Return**. To open Terminal: press **⌘ + Space**, type
`Terminal`, press **Return**. Paste with **⌘ + V**. Nothing here can damage your
Mac, and *Removing it* at the end undoes all of it.

## A1 — Get on the network

Connect to the VPN the way you normally do, or be on the office network.

There's nothing to check yet — A3 checks it for you, and it can tell the
difference between "not connected" and "connected but not trusted".

## A2 — Tell your Mac where the server is

Open **Terminal**, paste this single line, press **Return**:

```bash
sudo sh -c 'printf "\n172.20.1.10\tlegal-triage.internal.trinetix.net\n" >> /etc/hosts'
```

It asks for your Mac login password. **The password stays invisible as you type
it** — no dots, no stars. That's normal. Type it and press **Return**.

**Check it worked.** Paste this, press **Return**:

```bash
grep legal-triage /etc/hosts
```

You should get one line back:

```
172.20.1.10	legal-triage.internal.trinetix.net
```

Twice means you ran the first command twice — harmless, carry on. Nothing at all
means it didn't take: try again, and if it still doesn't, send us what Terminal
printed.

## A3 — Check you can reach the server

Open **Safari** or **Chrome** and go to:

**https://legal-triage.internal.trinetix.net/taskpane.html**

One of three things happens. Find yours:

| What you see | What it means | What to do |
|---|---|---|
| **A security / certificate warning** ("This Connection Is Not Private") | **Good news.** You reached our server. Your Mac just doesn't trust its certificate yet — that's exactly what A4 fixes. | Go to A4. |
| **"Can't connect", "took too long to respond", "server not found"** | You're not reaching the server. Almost always the VPN. | Reconnect the VPN and reload. If it still fails, tell us. |
| **The page loads with no warning** (it will probably look blank) | Your Mac already trusts the certificate. | **Skip A4** and go straight to A5. |

**There may be no padlock, and that's fine.** Chrome and Edge stopped showing
one — you'll see a small sliders / "tune" icon instead. What matters is that
there's **no full-page warning** and **no "Not secure" label** in the address bar.
(Clicking that icon should say *Connection is secure*.)

**Also expect the page itself to be empty.** A blank page is the normal result —
it's built to run inside Word, not in a browser. The only thing being tested here
is whether your Mac accepts the certificate.

**Don't click "Visit this website anyway" on the warning.** It won't help — Word
checks the certificate itself, separately from your browser, and clicking through
in the browser does nothing for Word. A4 is what Word needs.

## A4 — Tell your Mac to trust our certificate

In **Terminal**, paste this and press **Return**:

```bash
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/Downloads/caddy-root-ca.crt
```

It asks for your login password again (a dialog box may appear asking for it
instead — either is fine). **On success it prints nothing at all.** Silence here
means it worked.

`No such file or directory` means the certificate file isn't in your Downloads
folder — put it there and run the command again.

**Check it worked.** Go back to the browser tab from A3 and reload.

- **No warning** → you're done with the hard part. Go to A5. (A blank page with
  no warning is a pass — see the note in A3.)
- **Still a certificate warning** → stop here and tell us. Don't continue to A5;
  Word will only fail in a way that's harder to read than this page is.

## A5 — Install the assistant into Word

In **Terminal**, paste both of these lines together and press **Return**:

```bash
mkdir -p ~/Library/Containers/com.microsoft.Word/Data/Documents/wef
cp ~/Downloads/manifest.prod.xml ~/Library/Containers/com.microsoft.Word/Data/Documents/wef/legal-triage.manifest.xml
```

No password needed, and again — no output means it worked.

Then, in Word:

1. **Quit Word completely: ⌘ + Q.** Closing the document window is *not* enough;
   Word only notices the new add-in on a full restart.
2. Reopen Word and open any document.
3. On the **Home** tab, click **Add-ins** (the puzzle-piece icon).
4. In the dialog, click **My Add-ins**, then look for the **SHARED FOLDER**
   heading near the top. *(If clicking **Add-ins** opens a store panel instead of
   a dialog, click **Advanced** at the bottom of it first.)*
5. Click **Legal Triage**.
6. The panel opens down the right-hand side of the document.

If **Legal Triage** isn't under SHARED FOLDER, see *If something goes wrong* below.

**Now go to [Step 6](#step-6-everyone--put-your-name-in).**

---

# Part B — Installing on Windows

Three of these steps need **administrator rights**. If you don't have them, stop
and tell us rather than asking IT yourself — there's a better route for your
machine and we'd rather set that up than have you fight this one.

**How to open PowerShell as administrator:** click **Start**, type `PowerShell`,
then **right-click** *Windows PowerShell* and choose **Run as administrator**.
Click **Yes** on the prompt. The window title will say *Administrator*. Paste with
**Ctrl + V** (or right-click), run with **Enter**.

## B1 — Get on the network

Connect to the VPN the way you normally do, or be on the office network.

Nothing to check yet — B3 checks it for you, and it can tell the difference
between "not connected" and "connected but not trusted".

## B2 — Tell Windows where the server is

In the **administrator** PowerShell window, paste this single line and press
**Enter**:

```powershell
Add-Content -Path "$env:SystemRoot\System32\drivers\etc\hosts" -Value "`n172.20.1.10`tlegal-triage.internal.trinetix.net"
```

Nothing is printed on success.

> Those two backtick characters (`` ` ``) before `n` and `t` are meant to be
> there. Paste the line whole rather than retyping it.

**Check it worked.** Paste this, press **Enter**:

```powershell
Get-Content "$env:SystemRoot\System32\drivers\etc\hosts" | Select-String legal-triage
```

You should get one line back containing `172.20.1.10` and
`legal-triage.internal.trinetix.net`. Nothing at all means it didn't take — check
the window title really says *Administrator*, and try again.

If it says **access denied**, you're not running as administrator. Close the
window and reopen it with **Run as administrator**.

## B3 — Check you can reach the server

Open **Edge** or **Chrome** — **not Firefox**, which keeps its own separate list of
trusted certificates and will keep warning you even after B4 — and go to:

**https://legal-triage.internal.trinetix.net/taskpane.html**

One of three things happens. Find yours:

| What you see | What it means | What to do |
|---|---|---|
| **A certificate warning** ("Your connection isn't private", `NET::ERR_CERT_AUTHORITY_INVALID`) | **Good news.** You reached our server. Windows just doesn't trust its certificate yet — that's exactly what B4 fixes. | Go to B4. |
| **"Can't reach this page", "took too long to respond"** | You're not reaching the server. Almost always the VPN. | Reconnect the VPN and reload. If it still fails, tell us. |
| **The page loads with no warning** (it will probably look blank) | Windows already trusts the certificate. | **Skip B4** and go straight to B5. |

**There may be no padlock, and that's fine.** Chrome and Edge stopped showing
one — you'll see a small sliders / "tune" icon instead. What matters is that
there's **no full-page warning** and **no "Not secure" label** in the address bar.
(Clicking that icon should say *Connection is secure*.)

**Also expect the page itself to be empty.** A blank page is the normal result —
it's built to run inside Word, not in a browser. The only thing being tested here
is whether your PC accepts the certificate.

**Don't click "Continue to this site (unsafe)".** It won't help — Word checks the
certificate itself, separately from your browser. B4 is what Word needs.

## B4 — Tell Windows to trust our certificate

In the **administrator** PowerShell window, paste this and press **Enter**:

```powershell
certutil -addstore -f Root "$env:USERPROFILE\Downloads\caddy-root-ca.crt"
```

Look for **`CertUtil: -addstore command completed successfully.`** at the end of
what it prints. That's the confirmation.

- **`The system cannot find the file specified`** → the certificate file isn't in
  your Downloads folder. Put it there and run the command again.
- **`Access denied`** → the window isn't running as administrator. Reopen it with
  **Run as administrator**.

<details>
<summary>Prefer to click through it instead of pasting a command?</summary>

Double-click `caddy-root-ca.crt` in your Downloads folder →
**Install Certificate…** → Store Location: **Local Machine** → **Next** → *Place
all certificates in the following store* → **Browse…** → **Trusted Root
Certification Authorities** → **OK** → **Next** → **Finish** → **Yes** on the
security warning.

The **Local Machine** choice matters — *Current User* isn't enough for Word.
</details>

**Check it worked.** Go back to the browser tab from B3 and reload. You may need
to close the browser completely and reopen it first.

- **No warning** → you're done with the hard part. Go to B5. (A blank page with
  no warning is a pass — see the note in B3.)
- **Still a certificate warning** → stop here and tell us. Don't continue to B5.

## B5 — Install the assistant into Word

Unlike a Mac, Word on Windows won't load an add-in from an ordinary folder — it
insists on a **shared** folder. So this step makes a folder on your own PC, puts
the add-in file in it, shares it with yourself, and tells Word to trust it.

**You don't need anything from us for this** — the commands work out your own PC's
name themselves.

### B5.1 — Run this once

**Close Word first** — Word only reads this setting when it starts.

Then, in the **administrator** PowerShell window, paste the whole block below —
all of it, in one go — and press **Enter**:

```powershell
# Make the folder, put the add-in file in it, share it with your own account
New-Item -ItemType Directory -Force -Path C:\LegalTriage | Out-Null
Copy-Item "$env:USERPROFILE\Downloads\manifest.prod.xml" C:\LegalTriage\ -Force
if (-not (Get-SmbShare -Name LegalTriage -ErrorAction SilentlyContinue)) {
  New-SmbShare -Name LegalTriage -Path C:\LegalTriage -ChangeAccess "$env:USERDOMAIN\$env:USERNAME" | Out-Null
}

# Tell Word to trust that shared folder as an add-in catalogue.
# This ADDS one entry next to whatever is already there. It never edits or
# removes an existing one, and it does nothing at all if it already ran.
$url  = "\\$env:COMPUTERNAME\LegalTriage"
$root = "HKCU:\Software\Microsoft\Office\16.0\WEF\TrustedCatalogs"
if (-not (Test-Path $root)) { New-Item -Path $root -Force | Out-Null }
$existing = Get-ChildItem $root -ErrorAction SilentlyContinue |
  Where-Object { (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).Url -eq $url }
if (-not $existing) {
  $guid = [guid]::NewGuid().ToString('B')
  $key  = Join-Path $root $guid
  New-Item -Path $key | Out-Null
  New-ItemProperty -Path $key -Name Id    -Value $guid -PropertyType String -Force | Out-Null
  New-ItemProperty -Path $key -Name Url   -Value $url  -PropertyType String -Force | Out-Null
  New-ItemProperty -Path $key -Name Flags -Value 1     -PropertyType DWord  -Force | Out-Null
}

Write-Host "Done. Word will look for the add-in in: $url"
```

The only thing it prints is that last line, naming the folder Word will use. It
changes nothing you already have — see the drop-down below — and running it twice
does nothing the second time.

### B5.2 — Check what it just did

Paste this and press **Enter**:

```powershell
Get-SmbShare -Name LegalTriage | Format-List Name, Path
Test-Path "\\$env:COMPUTERNAME\LegalTriage\manifest.prod.xml"
Get-ChildItem HKCU:\Software\Microsoft\Office\16.0\WEF\TrustedCatalogs | ForEach-Object { Get-ItemProperty $_.PSPath | Select-Object Url, Flags }
```

You want all three of these:

| What you should see | What it means |
|---|---|
| `Name : LegalTriage` and `Path : C:\LegalTriage` | The shared folder exists |
| **`True`** | Word can actually read the add-in file **through the share** — this is the one that matters, because it's exactly what Word does |
| A row with your `Url` and `Flags : 1` | Word has been told to trust that folder, and to show it in the menu |

**If the middle line says `False`, stop here and tell us.** Word will not find the
add-in, and don't work around it — the reason is worth knowing before the rest of
the team hits it.

### B5.3 — Open it in Word

1. **Open Word** (if it was already open, close it completely and reopen — the
   shared folder is only picked up at startup).
2. On the **Home** tab, click **Add-ins**. This opens a store panel — click
   **Advanced** at the bottom of it to get the older **Office Add-ins** dialog.
   *(On some builds the route is **Insert → Add-ins → My Add-ins** instead. Either
   way, you're looking for the dialog that has tabs across the top.)*
3. Choose **SHARED FOLDER** at the top of that dialog.
4. Select **Legal Triage** and click **Add**. The panel opens down the right-hand
   side.

> **Can't find SHARED FOLDER?** It's the **Advanced** button that gets you there
> on current versions of Word — the store panel that opens first doesn't show it.
> This one catches everybody.

<details>
<summary>What did that command actually do to my PC?</summary>

Three things, all undone by *Removing it* at the end of this guide:

1. Created the folder `C:\LegalTriage` and copied `manifest.prod.xml` into it. The
   manifest is a small text file of web addresses — it is not the assistant, which
   stays on our server.
2. Shared that folder under the name `LegalTriage`, **to your own Windows account
   only** — nobody else on the network is granted access. (Word requires a
   *shared* folder and at least read/write access to it; an ordinary folder is
   refused, which is the whole reason for this step.)
3. Added one entry to your own user settings telling Word it may load add-ins from
   that folder (the same entry the **File → Options → Trust Center → Trusted
   Add-in Catalogs** screen writes when you fill it in by hand).

**What it does not touch.** Your documents, your templates and AutoText, your
Word settings, and any add-in you already have — including anything IT installed
for you. The Trust Center entry is *added alongside* whatever is already listed;
nothing existing is edited or removed. And the add-in itself never changes a
document on its own: it reads the open file when you ask it to, and every edit it
proposes arrives as a normal tracked change you can reject.

The one thing that does reach beyond this add-in is the optional cache-clearing
command in *Removing it*, and it says so there.
</details>

<details>
<summary>Doing it by hand instead, through Word's menus</summary>

If you'd rather click than paste, run only the **first four lines** of B5.1 (the
folder, the copy, the share — Word needs at least read/write on it), then in
Word:

1. **File → Options → Trust Center → Trust Center Settings… → Trusted Add-in
   Catalogs**.
2. In **Catalog Url**, type `\\` followed by your PC's name, then `\LegalTriage`
   — the **folder**, with no file name on the end. (`echo $env:COMPUTERNAME`
   prints your PC's name.)
3. **Add catalog** → tick **Show in Menu** → **OK** → **OK**.
4. Close Word completely, reopen, then carry on from **B5.3 step 2** above
   (Home → Add-ins → **Advanced** → SHARED FOLDER).
</details>

<details>
<summary>Some Word builds have a one-click shortcut</summary>

A few versions of Word show an **Upload My Add-in** link in the top-right of the
**Insert → Add-ins** dialog. If yours does, you can simply click it and choose
`manifest.prod.xml` from your Downloads folder — no folder, no share, no
administrator rights. It does the same job.

Most builds don't show it, which is why it isn't the main route here. If you
*do* see it, please tell us — we'd like to know which Word versions have it.
</details>

> **Is "Trusted Add-in Catalogs" greyed out, or does Word ignore the folder
> entirely?** Company policy is managing your Trust Center. Tell us — that machine
> needs IT to publish the add-in centrally, and there's nothing you can do from
> your side.

**Now go to [Step 6](#step-6-everyone--put-your-name-in).**

---

# Step 6 (everyone) — Put your name in

Whichever way you installed it: open the panel and click the **Preferences** tab.

1. In **Your name**, type your name.
2. Click **Save**.

This is how we know whose feedback is whose — there's no company login in the
assistant yet, so it can't work this out for itself. Please don't skip it; an
unnamed report is much harder for us to act on.

The large box underneath is yours too. Anything you write there is read on every
review and every chat — for example:

```
- Always flag uncapped indemnity as Red.
- Governing-law fallback is Delaware.
```

These shape what the assistant emphasises. They never override the firm
playbook — if your note and the playbook disagree, the playbook wins.

**One-off, done.** From now on the **Home** tab has a **Legal Triage** group with a
**Review** button that reopens the panel in any document. You never repeat the
steps above.

---

## What you'll see in the panel

Three tabs: **Findings**, **Chat**, **Preferences**.

### Findings

**Review this contract** reads the open document and triages it. Expect **30–60
seconds** — the model runs on our own hardware, so it's slower than the chat
assistants you're used to, and it is thinking about your whole contract.

You'll get a summary, a **Red and Missing Context** card (the blocking items),
per-clause findings, and **Business Questions**. On each finding:

- **Click the clause name** to jump to that clause in the document.
- **Comment in doc** adds a Word comment at that clause.
- **Accept redline** applies the suggested wording as a tracked change. It only
  appears when the finding quoted the exact current wording — when it's absent,
  the suggested wording is there for you to apply by hand.

### Chat

Ask anything about the open document — *"who signs this?"*, *"is the liability
cap our standard?"*, *"redline the termination notice to 60 days"*.

If you ask for a change, the answer comes back as a card with:

- **Apply with Track Changes** — makes the edit as a normal Word tracked change,
  so you can Accept or Reject it the usual way.
- **Go to** — shows you where it would land, without changing anything.
- **Discard** — throws the suggestion away. Nothing in the document changes.

### Finalize → clean copy

At the bottom of the panel. It **accepts every tracked change in the document —
not only the assistant's** — and turns Track Changes off, so you can send a clean
copy. It asks you to confirm first. **Save a copy before you use it** if you want
to keep the redlined version, then use Word's **File → Save As** for the clean one.

### Three notices worth recognising

| Notice | What it means |
|---|---|
| **"This document isn't saved yet."** (blue) | Save the file. Until you do, the review history, the chat and any feedback you send stop being connected to this contract the moment you close it. |
| **"Part of this document was not sent"** (red) | The contract was too long to send whole, so the review is incomplete. Tell us when you see this. |
| **"Condense earlier turns"** (a button, in a long chat) | Room is running out, and earlier chat is crowding out the contract itself. Clicking it shortens the earlier conversation. It usually happens on its own. |

### A tip about redline colours

Tracked insertions and deletions appear in **Word's own revision colours**, which
by default make both the same colour. If you'd rather have green insertions and
red deletions:

- **Windows:** **Review → Track Changes** (the small arrow / **Advanced Track
  Changes Options**) → set **Insertions: Green**, **Deletions: Red**.
- **Mac:** **Word → Preferences → Track Changes** → same two settings.

That's a setting on your own machine, not something the assistant controls.

---

## Telling us what it got wrong

This is the part of the pilot that matters most. **Work on real contracts the way
you normally would** — the point is to find where the assistant is wrong, not to
exercise every button.

**The ⚑ button** sits on every finding, every proposed edit and every chat reply.
Use it whenever something is wrong: a finding that isn't a real problem, an edit
that changes the wrong thing, an answer that's incorrect. One sentence is plenty
— *"this clause is fine as written"*, *"wrong field"*, *"we never require this"*.

**The "Send feedback" button at the top** is for what the assistant **missed** —
*"it didn't flag the assignment clause"*. Please use it. A missed issue leaves no
trace anywhere; if you don't tell us, nothing else can.

**You don't need to report anything else.** Which edits you Apply and which you
Discard is already recorded, so a Discard is itself a signal. Just work normally.

**What gets sent:** your note, the text of the document you're working on, and
the specific finding or edit you flagged. Your name is attached.

**Where it goes:** to the development team, so we can reproduce the exact case.
**It does not train the assistant** — flagging something will not change its
behaviour tomorrow. It gets the problem fixed properly instead.

> **Save your document before you close it.** On an unsaved document, Word can't
> store the id that ties your feedback to that contract, so reports from a
> closed-unsaved draft can't be grouped with later ones. The panel tells you when
> this applies — the blue *"This document isn't saved yet"* notice above the tabs.
> Save the file and it clears. No notice means you're fine.

---

## If something goes wrong

### Both platforms

| What you see | What's happening | What to do |
|---|---|---|
| **"Add-in could not be loaded"**, or a certificate error inside Word | Your laptop doesn't trust the certificate, or the server name isn't pointing anywhere | Redo the two permission steps (A2 + A4, or B2 + B4), then re-run the browser check (A3 / B3). Word will keep failing until that page loads without a warning. |
| **The panel is blank or grey** | Usually the same cause as above | Re-run the browser check. If that page is fine and the panel still isn't, restart Word once, then send us a screenshot. |
| **The panel loads but every action gives an error** | You can't reach the server — nearly always the VPN | Reconnect the VPN, then reload the browser-check page to confirm. |
| **You can't find the padlock** | Chrome and Edge don't show one any more | Nothing is wrong. Look for the *absence* of a warning instead — no full-page warning, no "Not secure" in the address bar. |
| **"Memory unavailable this turn"** (amber) | Our server has a temporary problem. Your answer is still correct, but this turn won't be remembered | Carry on; tell us if it keeps happening. |
| **A review takes several minutes** | A long contract, or the model is busy with someone else's document | Give it a couple of minutes. If it never returns, tell us roughly how long you waited. |
| **It worked yesterday, today Word rejects the certificate** | The server's certificate was regenerated at our end | Tell us — we'll send a new `caddy-root-ca.crt` and you redo A4 / B4 with it. Not your fault, and not fixable from your side. |

### Mac only

| What you see | What to do |
|---|---|
| **Legal Triage isn't under SHARED FOLDER** | Quit Word with **⌘ + Q** (not just the window) and reopen. If it's still missing, re-run A5 and check Terminal printed no error. |

### Windows only

| What you see | What to do |
|---|---|
| **You can't find SHARED FOLDER at all** | You're in the new store panel. Click **Advanced** at the bottom of it to reach the dialog that has SHARED FOLDER. |
| **Legal Triage isn't under SHARED FOLDER** | Close Word completely and reopen — the shared folder is only picked up at startup. Then re-run the B5.2 check; all three lines must be right. |
| **B5.2's middle line says `False`** | Word can't read the file through the share. Re-run B5.1 in an **administrator** window and watch for an error on the `New-SmbShare` line. |
| **`New-SmbShare` fails** | Windows file sharing is switched off. In the same administrator window: `Set-Service LanmanServer -StartupType Automatic; Start-Service LanmanServer`, then re-run B5.1. |
| **B5.2 shows no `Url`/`Flags` row** | The registry step didn't take. Re-run B5.1, or use the by-hand route in the B5.3 drop-down. (The `16.0` in that path covers Office 2016 and everything newer, including Microsoft 365 — if your Word is older, tell us.) |
| **"Trusted Add-in Catalogs" is greyed out, or Word ignores the folder** | Company policy manages your Trust Center. Tell us — your machine needs IT to publish the add-in centrally. |
| **No "Upload My Add-in" in the Add-ins dialog** | Expected on most builds — it's an optional shortcut, not the route. B5.1–B5.3 don't need it. |
| **`Access denied` from either PowerShell command** | The window isn't elevated. Reopen PowerShell with **Run as administrator** (the title bar should say *Administrator*). |
| **Firefox still warns about the certificate** | Expected — Firefox keeps its own list. It doesn't affect Word. Use Edge or Chrome for the check. |

**When you tell us, this is all we need:** a screenshot of the panel, what you
clicked, and roughly when. If a contract is involved, say which one — you don't
need to send the file.

---

## Removing it

### On a Mac

```bash
# 1. Remove the add-in from Word (then quit and reopen Word)
rm ~/Library/Containers/com.microsoft.Word/Data/Documents/wef/legal-triage.manifest.xml

# 2. Remove the server name
sudo sed -i '' '/legal-triage\.internal\.trinetix\.net/d' /etc/hosts
```

3. **Remove the certificate:** open **Keychain Access**, choose **System** on the
   left, then **Certificates**, find **Caddy Local Authority**, delete it.

### On Windows

1. **Remove the shared folder and the Word entry** — administrator PowerShell,
   paste the whole block:

   ```powershell
   Remove-SmbShare -Name LegalTriage -Force -ErrorAction SilentlyContinue
   Remove-Item C:\LegalTriage -Recurse -Force -ErrorAction SilentlyContinue
   Get-ChildItem HKCU:\Software\Microsoft\Office\16.0\WEF\TrustedCatalogs -ErrorAction SilentlyContinue |
     Where-Object { (Get-ItemProperty $_.PSPath).Url -like "*LegalTriage*" } |
     Remove-Item -Recurse -Force
   ```

   Then restart Word. (If you used the **Upload My Add-in** shortcut instead:
   **Home → Add-ins → Advanced → My Add-ins**, right-click **Legal Triage** →
   **Remove**.)

   If **Legal Triage** still appears after restarting Word, Word is holding it in
   its own cache. Clear it with:

   ```powershell
   Remove-Item "$env:LOCALAPPDATA\Microsoft\Office\16.0\Wef" -Recurse -Force -ErrorAction SilentlyContinue
   ```

   ⚠ **This one is broader than the rest.** It empties Word's cache for **all**
   Office add-ins on your account, not just this one — anything they had saved
   locally is cleared, and other sideloaded add-ins may need re-adding. Add-ins
   installed for you by IT reappear by themselves. Use it only if Legal Triage is
   still showing after a restart, and if you're unsure, ask us first.

2. **Remove the server name** — administrator PowerShell, one line:

   ```powershell
   $h = "$env:SystemRoot\System32\drivers\etc\hosts"; (Get-Content $h) | Where-Object { $_ -notmatch 'legal-triage\.internal\.trinetix\.net' } | Set-Content $h
   ```

3. **Remove the certificate:** press **Start**, type `certlm.msc`, press
   **Enter** → **Trusted Root Certification Authorities → Certificates** → find
   **Caddy Local Authority**, right-click → **Delete**. Delete only that one.

---

## Questions we get asked

**Can I use this in Word in my browser, or on a document opened from SharePoint
in the browser?**
Not yet — desktop Word only, on Windows or Mac. Our Microsoft 365 tenant no longer
lets people add an add-in to browser Word by hand, so that version needs an
administrator to publish it centrally. It's requested. You can still work on a
file that lives in SharePoint or OneDrive — just open it in the desktop Word app.

**Does my contract leave the company?**
No. The server is internal, the model runs on our own hardware, and nothing is
sent to an outside service.

**Is it reading my documents in the background?**
No. It only reads the open document when you click **Review this contract** or
ask something in **Chat**.

**Can I trust its answers?**
Treat it as a first-pass reviewer, not an authority. It works from the firm
playbook, and it is wrong often enough that the ⚑ button is the most important
control in the panel.

**Do I have to do this again on my other laptop?**
Yes — all of it, once per machine. Your name and your preferences follow your
login, not the machine, but the certificate and the add-in are per-laptop.

---

*Setting this up for someone, rather than following it?* The server-side half —
choosing the hostname, extracting the certificate file, rendering the manifest,
and (for Windows B5b) providing the shared-folder path — is **Step 7** of
[`docs/deploy-vm.md`](deploy-vm.md).
