import os
import re
import json
import datetime
import requests
import logging
from bs4 import BeautifulSoup

# --- 設定 ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
REPORT_FILE = os.path.join(SCRIPT_DIR, "index.html") 
LOG_FILE = os.path.join(SCRIPT_DIR, "dlsite_scraper.log")
DEBUG_HTML_FILE = os.path.join(SCRIPT_DIR, "debug_error.html")

URL = "https://www.dlsite.com/maniax/ranking?date=30d"

# ログの設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

os.makedirs(DATA_DIR, exist_ok=True)

def fetch_ranking():
    """セッションを維持したまま、リダイレクト先にもCookieを保持してページを取得する"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
        'Referer': 'https://www.dlsite.com/'
    }
    
    logging.info("DLsiteからWebページを取得しています（セッション開始）...")
    
    try:
        session = requests.Session()
        session.cookies.set('adultchecked', '1', domain='.dlsite.com')
        session.cookies.set('work_view', '0', domain='.dlsite.com')
        session.cookies.set('locale', 'ja-jp', domain='.dlsite.com')
        session.cookies.set('localesuggested', 'true', domain='.dlsite.com')
        
        response = session.get(URL, headers=headers, timeout=15)
        logging.info(f"HTTPレスポンスステータス: {response.status_code}")
        response.raise_for_status()
        
        return response.text
    except Exception as e:
        logging.error("Webページの取得中に通信エラーが発生しました:", exc_info=True)
        return None

def parse_ranking(html):
    """HTMLから4分類（リスト形式）のランキングデータを抽出する"""
    soup = BeautifulSoup(html, 'html.parser')
    
    # 年齢確認ゲートに引っかかっていないかチェック
    if "18歳以上ですか" in html and "adultchecked" in html:
        logging.error("年齢確認ページ（ゲート画面）が表示されています。Cookieの認証迂回に失敗している可能性があります。")
        save_debug_html(html)
        return None

    # ランキングトップページに並ぶ 4つの ul.ranking_top_worklist タグを検出
    lists = soup.find_all('ul', class_='ranking_top_worklist')
    
    if not lists:
        logging.warning("ページ内に 'ranking_top_worklist' クラスを持つリストが見つかりませんでした。")
        save_debug_html(html)
        return None

    # 各リストに対応するカテゴリ名（左から順に並んでいます）
    categories = ["総合", "マンガ・CG", "ゲーム・動画", "ボイス・ASMR・音楽"]
    parsed_data = {}
    
    for idx, ul in enumerate(lists):
        if idx >= len(categories):
            category_name = f"その他分類_{idx+1}"
        else:
            category_name = categories[idx]
            
        logging.info(f"分類「{category_name}」の解析を開始します。")
        items = []
        
        # リストアイテム（作品行）の抽出
        lis = ul.find_all('li', class_=re.compile(r'ranking_top_worklist_item'))
        for li in lis:
            # 1. 順位のパース
            rank_no = None
            rank_el = li.find(class_=re.compile(r'rank'))
            if rank_el:
                rank_text = rank_el.get_text(strip=True)
                rank_match = re.search(r'\d+', rank_text)
                if rank_match:
                    rank_no = int(rank_match.group(0))
            if not rank_no:
                rank_no = len(items) + 1
                
            # 2. 作品名、URL、RJコードのパース
            work_name_el = li.find(class_='work_name')
            if not work_name_el:
                continue
                
            a_tag = work_name_el.find('a')
            if not a_tag:
                continue
                
            title = a_tag.get_text(strip=True)
            work_url = a_tag['href']
            if work_url.startswith('//'):
                work_url = 'https:' + work_url
            elif work_url.startswith('/'):
                work_url = 'https://www.dlsite.com' + work_url
                
            rj_match = re.search(r'product_id/([A-Z]{2}\d+)', work_url)
            rj_code = rj_match.group(1) if rj_match else ""
            
            # 3. サークル名のパース
            maker_el = li.find(class_='maker_name')
            maker_name = ""
            maker_url = ""
            if maker_el:
                maker_a = maker_el.find('a')
                if maker_a:
                    maker_name = maker_a.get_text(strip=True)
                    maker_url = maker_a['href']
                    if maker_url.startswith('//'):
                        maker_url = 'https:' + maker_url
                    elif maker_url.startswith('/'):
                        maker_url = 'https://www.dlsite.com' + maker_url
                else:
                    maker_name = maker_el.get_text(strip=True)
                    
            # 4. サムネイル画像（Vueのカスタムタグ :thumb-candidates から抽出）
            img_url = ""
            thumb_component = li.find('thumb-with-ng-filter')
            if thumb_component:
                candidates_attr = thumb_component.get(':thumb-candidates')
                if candidates_attr:
                    # 配列形式の文字列から、最初の画像URLを正規表現で抽出
                    img_match = re.search(r"'(//img\.dlsite\.jp/[^']+)'", candidates_attr)
                    if not img_match:
                        img_match = re.search(r'"(//img\.dlsite\.jp/[^"]+)"', candidates_attr)
                    if img_match:
                        img_url = 'https:' + img_match.group(1)
            
            # フォールバック（通常のimgタグから探す）
            if not img_url:
                img_tag = li.find('img')
                if img_tag:
                    img_url = img_tag.get('data-src') or img_tag.get('data-original') or img_tag.get('src', '')
                    if img_url.startswith('//'):
                        img_url = 'https:' + img_url
            
            items.append({
                "rank": rank_no,
                "id": rj_code,
                "title": title,
                "url": work_url,
                "circle": maker_name,
                "circle_url": maker_url,
                "image": img_url
            })
            
        parsed_data[category_name] = items[:30]
        logging.info(f"分類「{category_name}」から {len(items[:30])} 件のデータを正常に抽出しました。")
        
    return parsed_data

def save_debug_html(html):
    """エラー解析用にHTMLを保存する"""
    try:
        with open(DEBUG_HTML_FILE, "w", encoding="utf-8") as f:
            f.write(html)
        logging.info(f"エラー分析用の生HTMLを保存しました: {DEBUG_HTML_FILE}")
    except Exception as e:
        logging.error(f"デバッグ用ファイルの書き出しに失敗しました: {e}")

def aggregate_weekly():
    """過去7日間のデータを集計する"""
    today = datetime.date.today()
    week_dates = [today - datetime.timedelta(days=i) for i in range(7)]
    
    aggregated = {}
    files_processed = 0
    
    for date in week_dates:
        filename = os.path.join(DATA_DIR, f"{date.strftime('%Y-%m-%d')}.json")
        if os.path.exists(filename):
            files_processed += 1
            with open(filename, 'r', encoding='utf-8') as f:
                day_data = json.load(f)
                
            for category, items in day_data.items():
                if category not in aggregated:
                    aggregated[category] = {}
                    
                for item in items:
                    work_id = item['id']
                    if not work_id:
                        continue
                        
                    if work_id not in aggregated[category]:
                        aggregated[category][work_id] = {
                            "id": work_id,
                            "title": item['title'],
                            "url": item['url'],
                            "circle": item['circle'],
                            "circle_url": item['circle_url'],
                            "image": item['image'],
                            "highest_rank": item['rank'],
                            "days_appeared": 1
                        }
                    else:
                        aggregated[category][work_id]["days_appeared"] += 1
                        if item['rank'] < aggregated[category][work_id]["highest_rank"]:
                            aggregated[category][work_id]["highest_rank"] = item['rank']
                            
    if files_processed == 0:
        logging.warning("集計対象となるJSONファイルが data フォルダ内に見つかりません。")
        return None
        
    logging.info(f"過去 {files_processed} 日分のデータを正常に集計しました。")
    return aggregated

def generate_html(aggregated):
    """HTMLレポートを書き出す"""
    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=6)
    date_range_str = f"{start_date.strftime('%Y/%m/%d')} ～ {today.strftime('%Y/%m/%d')}"
    
    html_content = f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>DLsite Maniax 週間統合ランキング</title>
    <style>
        body {{ font-family: sans-serif; background: #121212; color: #e0e0e0; padding: 20px; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .category-section {{ background: #1e1e1e; padding: 20px; border-radius: 8px; margin-bottom: 40px; border: 1px solid #2e2e2e; }}
        .category-title {{ font-size: 1.5rem; color: #ffffff; margin-top: 0; margin-bottom: 20px; border-left: 5px solid #3b82f6; padding-left: 10px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 20px; }}
        .card {{ background: #262626; padding: 15px; border-radius: 6px; border: 1px solid #2e2e2e; display: flex; flex-direction: column; }}
        .card-header {{ display: flex; gap: 8px; margin-bottom: 12px; }}
        .badge {{ font-size: 0.75rem; font-weight: bold; padding: 3px 8px; border-radius: 4px; color: #ffffff; }}
        .badge-rank {{ background: #ef4444; }}
        .badge-days {{ background: #10b981; }}
        .badge-id {{ background: #4b5563; }}
        .card-body {{ display: flex; gap: 12px; flex-grow: 1; }}
        .thumb-container {{ width: 70px; height: 70px; flex-shrink: 0; border-radius: 4px; overflow: hidden; background: #1a1a1a; border: 1px solid #2e2e2e; }}
        .thumb {{ width: 100%; height: 100%; object-fit: cover; }}
        .info {{ display: flex; flex-direction: column; justify-content: flex-start; min-width: 0; }}
        .work-title {{ font-size: 0.85rem; font-weight: bold; margin: 0 0 6px 0; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
        .work-title a {{ color: #ffffff; text-decoration: none; }}
        .work-title a:hover {{ color: #3b82f6; text-decoration: underline; }}
        .circle-name {{ font-size: 0.8rem; color: #aaa; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
        .circle-name a {{ color: #aaa; text-decoration: none; }}
        .circle-name a:hover {{ color: #3b82f6; }}
        footer {{ text-align: center; margin-top: 50px; color: #aaa; font-size: 0.8rem; border-top: 1px solid #2e2e2e; padding-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>DLsite Maniax 週間統合ランキング</h1>
        <p>対象期間: {date_range_str} (期間中に一度でもTop30にランクインした全作品)</p>
        <main>
"""
    for category, works in aggregated.items():
        sorted_items = sorted(works.values(), key=lambda x: (x['highest_rank'], -x['days_appeared']))
        html_content += f"""
            <section class="category-section">
                <h2 class="category-title">{category} <span style="font-size: 1rem; color: #aaa; font-weight: normal;">(計 {len(sorted_items)}作品)</span></h2>
                <div class="grid">
        """
        for item in sorted_items:
            img_src = item['image'] if item['image'] else "https://www.dlsite.com/images/web/home/no_img_mini.gif"
            html_content += f"""
                    <div class="card">
                        <div class="card-header">
                            <span class="badge badge-rank">最高 {item['highest_rank']}位</span>
                            <span class="badge badge-days">出現 {item['days_appeared']}日</span>
                            <span class="badge badge-id">{item['id']}</span>
                        </div>
                        <div class="card-body">
                            <div class="thumb-container">
                                <img class="thumb" src="{img_src}" loading="lazy">
                            </div>
                            <div class="info">
                                <h3 class="work-title">
                                    <a href="{item['url']}" target="_blank" rel="noopener noreferrer">{item['title']}</a>
                                </h3>
                                <div class="circle-name">
                                    <a href="{item['circle_url']}" target="_blank" rel="noopener noreferrer">{item['circle']}</a>
                                </div>
                            </div>
                        </div>
                    </div>
            """
        html_content += "</div></section>"
        
    html_content += f"""</main>
        <footer>
            <p>生成日時: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
        </footer>
    </div>
</body>
</html>"""
    
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html_content)
    logging.info(f"統合HTMLレポートを更新しました: {REPORT_FILE}")

def main():
    logging.info("=== DLsite Ranking Scraper 処理開始 ===")
    try:
        # 1. データのダウンロード
        html = fetch_ranking()
        if not html:
            logging.error("HTMLを取得できなかったため、処理を中断します。")
            return
            
        # 2. データのパース
        today_data = parse_ranking(html)
        if not today_data:
            logging.error("パース結果が空のため、保存処理をスキップします。")
            return
            
        # 3. JSON保存
        today_str = datetime.date.today().strftime('%Y-%m-%d')
        today_file = os.path.join(DATA_DIR, f"{today_str}.json")
        with open(today_file, 'w', encoding='utf-8') as f:
            json.dump(today_data, f, ensure_ascii=False, indent=2)
        logging.info(f"本日のデータを保存しました: {today_file}")
        
        # 4. 日曜日判定、または動作確認用の週次レポート作成
        if datetime.date.today().weekday() == 6:
            logging.info("本日は日曜日です。過去1週間の統合集計処理を開始します...")
            weekly_data = aggregate_weekly()
            if weekly_data:
                generate_html(weekly_data)
        else:
            logging.info("本日は日曜日ではないため、日次データの保存のみで正常終了します。")
            
    except Exception as e:
        logging.critical("実行中に予期せぬ重大な例外が発生しました:", exc_info=True)
    finally:
        logging.info("=== DLsite Ranking Scraper 処理終了 ===")

if __name__ == "__main__":
    main()