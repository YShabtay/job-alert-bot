# בוט משרות יומי לטלגרם

הבוט מחפש משרות סטודנט וג'וניור בישראל בחיפוש המשרות הציבורי של LinkedIn (בלי מפתח ובלי מכסה), מסנן אותן ושולח לטלגרם רק משרות שעוד לא נשלחו. חיפוש בגוגל דרך SerpApi קיים כאופציה, וכבוי כברירת מחדל.

## 1. יצירת בוט בטלגרם (2 דקות)

1. בטלגרם, חפשו את **@BotFather** (עם וי כחול) ולחצו Start.
2. שלחו `/newbot`, בחרו שם (למשל `My Jobs`) ו-username שמסתיים ב-`bot` (למשל `yogev_jobs_bot`).
3. BotFather ישלח **Bot Token** בצורה `123456789:ABC...`. זה ה-`TELEGRAM_BOT_TOKEN`.
4. פתחו את הבוט החדש ושלחו לו הודעה כלשהי (למשל `hi`). **בלי השלב הזה אי אפשר לקבל את ה-Chat ID.**
5. את ה-Chat ID מקבלים בסעיף 3 בעזרת `--get-chat-id`.

## 2. מפתח SerpApi (אופציונלי)

נדרש רק אם מפעילים `USE_GOOGLE_SEARCH = True`. נרשמים ב-https://serpapi.com ומעתיקים את ה-API Key. התוכנית החינמית כוללת 250 חיפושים בחודש.

## 3. התקנה והרצה

```bash
cd "/Users/yogevshabtay/Projects/Find Job"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt      # requests, python-dotenv
cp .env.example .env                 # ממלאים את TELEGRAM_BOT_TOKEN (SERPAPI_KEY רק לחיפוש בגוגל)
python job_bot.py --get-chat-id      # מדפיס את ה-Chat ID ← מעתיקים ל-.env
python job_bot.py --test             # הודעת בדיקה אמורה להגיע לטלגרם
python job_bot.py --dry-run          # מחפש ומדפיס, בלי לשלוח ובלי לשמור
python job_bot.py                    # ההרצה האמיתית
```

## 4. התאמת החיפוש

עורכים את הבלוק `Search settings` בראש `job_bot.py`:
- `LINKEDIN_KEYWORDS`: מילות החיפוש ב-LinkedIn (אין מגבלה)
- `LINKEDIN_POSTED_WITHIN`: חלון הזמן (ברירת מחדל: 3 ימים אחרונים)
- `USE_GOOGLE_SEARCH`, `SEARCH_QUERIES`, `SEARCH_TIME`: חיפוש בגוגל דרך SerpApi (אופציונלי)
- `ROLE_KEYWORDS` + `LEVEL_KEYWORDS`: משרה עוברת רק אם יש בה מילה מכל אחת משתי הקבוצות (תחום + רמת ניסיון)
- `EXCLUDE_KEYWORDS`: פסילה מיידית (Senior, תפ"י, שירות לקוחות, מכירות...)

## 5. הרצה אוטומטית כל יום (cron)

```bash
crontab -e
```
מוסיפים את השורה (כל יום ב-09:00):
```
0 9 * * * cd "/Users/yogevshabtay/Projects/Find Job" && .venv/bin/python job_bot.py >> job_bot.log 2>&1
```
cron לא ירוץ אם המחשב כבוי או ישן בשעה הזו.

## קבצים

| קובץ | תפקיד |
|---|---|
| `job_bot.py` | הסקריפט |
| `.env` | מפתחות סודיים (לא לשתף ולא להעלות ל-git) |
| `seen_jobs.json` | משרות שכבר נשלחו (נוצר אוטומטית, נמחק אחרי 90 יום) |
