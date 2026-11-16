package com.akkr.overlay

/**
 * 글자 정리. PC판 overlay/matcher.py의 normalize()·skeleton()과 똑같아야
 * 사전(export_dict.py가 PC판 정리 결과를 키로 저장)과 맞는다.
 */
object Text {
    val HAN = Regex("[一-鿿]")
    // 화면에 보이지 않는 서식 태그 (<@ba.vup>, </>, <color=...> 등)
    private val TAG = Regex("<[@$/][^<>]{0,40}>|</?color[^<>]{0,20}>|</?[ib]>|</>")
    private val PLACEHOLDER = Regex("\\{[^{}]*\\}")
    val NUMBER = Regex("\\d+(?:\\.\\d+)?%?")
    val DIGITS = Regex("\\d+")
    // 한자 숫자: 비슷한 문장 매칭에서 이게 다르면 뜻이 다름 (周二三六日开放 ≠ 周三四六日开放)
    val CN_NUM = Regex("[一二三四五六七八九十百千两]")
    // OCR이 헷갈리기 쉬운 문장부호를 통일
    private val PUNCT = mapOf(
        '…' to '.', '·' to '.', '，' to ',', '。' to '.', '！' to '!', '？' to '?', '：' to ':', '；' to ';',
        '“' to '"', '”' to '"', '‘' to '\'', '’' to '\'', '（' to '(', '）' to ')', '—' to '-', '–' to '-',
        '【' to '[', '】' to ']', '《' to '<', '》' to '>', '〈' to '<', '〉' to '>', '×' to 'x',
    )
    private val SPACE_DOTS = Regex("[\\s.]+")
    // 글머리표·아이콘이 글자로 잘못 읽힌 것 (◆, √, →, 돋보기 아이콘의 Q, 동그라미 숫자 등)
    val LEADING_JUNK = Regex("^(?:[^\\w一-鿿\\[(<]|[A-Za-z](?=[一-鿿])|[①-⑳])+")
    // OCR이 서로 헷갈리는 한자 (PC판 CONFUSABLE과 같게)
    private val CONFUSABLE = listOf(
        "干千于", "已己巳", "未末", "人入", "土士", "日曰", "戊戍戌成咸", "大太犬", "王玉主", "刀力",
        "免兔", "折拆", "徒徙", "历厉", "候侯", "博傅", "摇遥瑶", "视观", "烬煜炽焖", "欸欲",
        "挑姚", "傀愧倪", "引弓", "丛从", "凛漂", "瞥警聲声", "作乍", "敌文", "临高", "中史", "了子", "抉块", "瞌嗑", "物勿", "嗯想",
        // 폰(ML Kit)에서 자주 헷갈리는 글자 (集戌战略, 新人向调组合包, 住务, 公开招券)
        "同向", "任住", "募券导", "剩利", "合台",
        // PC판과 같게: 招募가 超募로, 공지의 오퍼레이터 이름 弑君者·止颂이 試君者·上颂으로
        "超招", "弑試试", "止上",
    )
    val CONFUSE: Map<Char, String> = buildMap { for (g in CONFUSABLE) for (c in g) put(c, g) }
    // 아이콘이 한자로 잘못 읽힐 때 자주 나오는 글자 (앞에 붙음: 图较慢 → 较慢)
    val ICON_CHARS = "图区回向三囗口日目田凶品吕亘目旦".toSet()
    val NOISE_CHARS = ICON_CHARS - "日回三".toSet() + "仓四".toSet()
    /** 홀로 읽히면 아이콘으로 보는 한 글자 (홈 친구·파일 버튼 아래 아이콘을 '人'으로 읽음). PC판 LONE_ICON */
    val LONE_ICON = ICON_CHARS + '人'
    val UNIT_CHARS = "天日时分秒次个名级层格倍张份回号期周年月第星阶".toSet()
    // 숫자 옆 한 글자 단위: 한섭 번역 틀에 단위가 없을 때 붙임 ('{0}天后开放' → '{0} 후 개방')
    val UNITS = linkedMapOf(
        "分钟" to "분", "小时" to "시간", "秒" to "초", "天" to "일", "星" to "성", "次" to "회", "层" to "층",
        "格" to "칸", "个" to "개", "名" to "명", "级" to "레벨", "倍" to "배", "张" to "장", "份" to "개", "回" to "회",
    )

    // 폰 OCR(ML Kit)이 가끔 번체자로 읽는 글자 → 간체자 (感謝庆典, 特別招募, 视相博物館)
    private const val TRAD = "萬與專業東絲兩嚴喪個豐臨為爲麗舉麼義烏樂喬習鄉書買亂爭虧雲亞產親億僅從侖倉儀們價眾優會傘偉傳傷倫偽體餘傭俠側僑係債傾償儲兒兌黨蘭關興養獸內岡冊寫軍農馮衝決況凍淨涼減湊凜幾鳳憑凱擊劃劉則剛創刪別劑劍劇勸辦務動勵勁勞勢勳勻區醫華協單賣盧衛卻廠廳歷厲壓厭縣參雙發變敘疊葉號嘆嚇呂嗎啞員聽啟吳嗚鳴響喚圍園圓國圖團聖場壞塊堅壇墳墜執擴掃揚擾撫搶護報擔擬擁攔撥擇掛撓擋擠揮損換據擲撐攜搖攝擺撲樸機權殺雜條來楊極構槍楓櫃標棧欄樹樣橋檔夢檢樓歡歐歲歸殘殲毀氣漢湯溝沒淚潑澤潔灑濁測濟渾濃濤漲漁淺滲溫滿濾濫灣濕潰濺燈靈爐點煉爛煩燒熱煥愛爺牆狀猶獨獄獲豬貓獻環現瑣電畫暢當療瘋盤監蓋盜睜礦碼確礙禮禍離禿種積稱穩窮窩競筆節範築簡籌簽類糧緊級紀紅約納紙紛線練組細終經結給絕統絡繼績續維綠網緣編緩縮總縱織繞繪罰羅聯聲聰職腦臉臟膽艦藝莊萊蓮藥蘇蟲蝦蠻補裝製襲規視覺覽觀觸計訂認討讓訓議記講許論設訪證評識詞譯試詩話該詳語誤說請諸讀調談謀謎謝謹豈貝負財貢貨質貧購貴費貿資賊賓賞賬賽贏贈趕趙躍蹤車軌軟輪轉輕載較輔輛輸轟辭這進遠運違連遲選遺遞邊鄰醜釋裡鑒針釘鈴鉛銀銅銳錢鋼錄錯鍵鍊鏈鎖鐘鐵鑰長門閃閉問闖閒間閱闊隊陽陰階際陸險隨隱難雖雞霧靜韓韻頁頂項順須預頓領頭頻題額顏願顯風飄飛飯飲飽飾餅館餓馬馳駐駕驅騎驗驚髮鬥鬧魚魯鮮鯨鳥鴉鷹鹽麥黃齊齒龍龜導獎戰戲慶惡應態憶憂懷懸戀鬆陳濱帥師廣慣壽糾紋純紗紡綁綜綿緒緯縫繩蠶蠟襯訊託詢誌誕誠課諾謊譜讚貫販貼貸賀賠賢賭賴贊輩輝轄遜遷錦鍋鎮鏡鑄閣闆陣隸靂韋頸顆顧颱饒馭駁騙騰驕髒鬍鮑鴨鵝鶴麵黴査採後裏隻臺夠準適"
    private const val SIMP = "万与专业东丝两严丧个丰临为为丽举么义乌乐乔习乡书买乱争亏云亚产亲亿仅从仑仓仪们价众优会伞伟传伤伦伪体余佣侠侧侨系债倾偿储儿兑党兰关兴养兽内冈册写军农冯冲决况冻净凉减凑凛几凤凭凯击划刘则刚创删别剂剑剧劝办务动励劲劳势勋匀区医华协单卖卢卫却厂厅历厉压厌县参双发变叙叠叶号叹吓吕吗哑员听启吴呜鸣响唤围园圆国图团圣场坏块坚坛坟坠执扩扫扬扰抚抢护报担拟拥拦拨择挂挠挡挤挥损换据掷撑携摇摄摆扑朴机权杀杂条来杨极构枪枫柜标栈栏树样桥档梦检楼欢欧岁归残歼毁气汉汤沟没泪泼泽洁洒浊测济浑浓涛涨渔浅渗温满滤滥湾湿溃溅灯灵炉点炼烂烦烧热焕爱爷墙状犹独狱获猪猫献环现琐电画畅当疗疯盘监盖盗睁矿码确碍礼祸离秃种积称稳穷窝竞笔节范筑简筹签类粮紧级纪红约纳纸纷线练组细终经结给绝统络继绩续维绿网缘编缓缩总纵织绕绘罚罗联声聪职脑脸脏胆舰艺庄莱莲药苏虫虾蛮补装制袭规视觉览观触计订认讨让训议记讲许论设访证评识词译试诗话该详语误说请诸读调谈谋谜谢谨岂贝负财贡货质贫购贵费贸资贼宾赏账赛赢赠赶赵跃踪车轨软轮转轻载较辅辆输轰辞这进远运违连迟选遗递边邻丑释里鉴针钉铃铅银铜锐钱钢录错键链链锁钟铁钥长门闪闭问闯闲间阅阔队阳阴阶际陆险随隐难虽鸡雾静韩韵页顶项顺须预顿领头频题额颜愿显风飘飞饭饮饱饰饼馆饿马驰驻驾驱骑验惊发斗闹鱼鲁鲜鲸鸟鸦鹰盐麦黄齐齿龙龟导奖战戏庆恶应态忆忧怀悬恋松陈滨帅师广惯寿纠纹纯纱纺绑综绵绪纬缝绳蚕蜡衬讯托询志诞诚课诺谎谱赞贯贩贴贷贺赔贤赌赖赞辈辉辖逊迁锦锅镇镜铸阁板阵隶雳韦颈颗顾台饶驭驳骗腾骄脏胡鲍鸭鹅鹤面霉查采后里只台够准适"
    private val T2S: Map<Char, Char> = TRAD.indices.associate { TRAD[it] to SIMP[it] }

    /** OCR이 읽은 글의 번체자를 간체자로 (사전 키는 간체자). 사전 쪽 문장에는 쓰지 않는다. */
    fun simplify(s: String): String = if (s.none { it in T2S }) s else String(CharArray(s.length) { T2S[s[it]] ?: s[it] })

    fun normalize(s: String): String {
        val t = TAG.replace(s, "").replace("\\n", "").replace("\n", "").replace("一一", "——")
        val sb = StringBuilder(t.length)
        for (c in t) sb.append(PUNCT[c] ?: c)
        return SPACE_DOTS.replace(sb) { if ('.' in it.value) "." else "" }
    }

    /** 숫자와 변수를 #로 바꾼 틀. 사전 원문은 {변수}, 화면은 실제 숫자이므로 둘 다 #가 된다. */
    fun skeleton(s: String): String = NUMBER.replace(PLACEHOLDER.replace(normalize(s), "#"), "#")

    fun hasHan(s: String) = HAN.containsMatchIn(s)
    fun hanCount(s: String) = HAN.findAll(s).count()
    fun digits(s: String) = DIGITS.findAll(s).map { it.value }.toList()
    fun cnNums(s: String) = CN_NUM.findAll(s).map { it.value }.toList()

    /** rapidfuzz의 fuzz.ratio와 같은 값: 2 × 최장 공통 부분열 / 두 길이의 합 × 100 */
    fun ratio(a: String, b: String): Int {
        if (a.isEmpty() && b.isEmpty()) return 100
        val prev = IntArray(b.length + 1)
        val cur = IntArray(b.length + 1)
        for (i in 1..a.length) {
            for (j in 1..b.length) {
                cur[j] = if (a[i - 1] == b[j - 1]) prev[j - 1] + 1 else maxOf(prev[j], cur[j - 1])
            }
            cur.copyInto(prev)
        }
        return (200.0 * prev[b.length] / (a.length + b.length)).toInt()
    }
}
