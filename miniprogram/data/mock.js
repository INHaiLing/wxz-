// 仅为原型中的可辨识内容提供演示。正式题库可通过相同结构替换。
const config = {
  examDate: '2027-03-28', // 示例日期，不代表官方考试安排。
  dailyTarget: 20
};

const categories = [
  { id: 'pre-qin', name: '先秦文学' },
  { id: 'qin-han', name: '秦汉文学' },
  { id: 'wei-jin', name: '魏晋南北朝文学' },
  { id: 'tang', name: '唐代文学' },
  { id: 'song', name: '宋代文学' },
  { id: 'yuan-ming-qing', name: '元明清文学' },
  { id: 'modern', name: '现当代文学' },
  { id: 'foreign', name: '外国文学' }
];

const articleRows = [
  ['guaren', '寡人之于国也'], ['quanxue', '劝学'], ['shishuo', '师说'],
  ['jiantaizong', '谏太宗十思疏'], ['tengwang', '滕王阁序'], ['yueyang', '岳阳楼记'],
  ['zuiweng', '醉翁亭记'], ['lijiangjun', '李将军列传'], ['liuguo', '六国论'],
  ['chenqing', '陈情表'], ['lanting', '兰亭集序'], ['qiushui', '秋水'],
  ['lunyili', '论毅力'], ['dengxia', '灯下漫笔'], ['zhengbo', '郑伯克段于鄢'],
  ['fengxuan', '冯谖客孟尝君'], ['zhangzhongcheng', '张中丞传后叙'],
  ['zhongshu', '种树郭橐驼传'], ['baoliu', '报刘一丈书'], ['maling', '马伶传'],
  ['pending-21', '待补充篇目', true], ['guanshanyue', '关山月'],
  ['baoyu', '宝玉挨打'], ['fengbo', '风波'], ['pending-25', '待补充篇目', true]
];
const articles = articleRows.map((row, index) => ({
  id: row[0], title: row[1], index: index + 1, placeholder: Boolean(row[2])
}));

const questions = [
  { id: 'lit-shijing-collection', source: 'literature', categoryId: 'pre-qin', type: 'fact', tag: '文学常识', stem: '《诗经》是我国第一部{{0}}诗歌总集，收录西周至春秋诗歌{{1}}篇，又称{{2}}。', answers: ['民间', '305', '诗三百'] },
  { id: 'lit-shijing-six', source: 'literature', categoryId: 'pre-qin', type: 'fact', tag: '文学常识', stem: '《诗经》体例分为{{0}}、{{1}}、{{2}}，表现手法为{{3}}、{{4}}、{{5}}，合称{{6}}。', answers: ['风', '雅', '颂', '赋', '比', '兴', '六义'] },
  { id: 'lit-confucius', source: 'literature', categoryId: 'pre-qin', type: 'fact', tag: '文学常识', stem: '孔子是春秋时期{{0}}国人，{{1}}学派创始人，思想核心是{{2}}。', answers: ['鲁', '儒家', '仁'] },
  { id: 'lit-analects', source: 'literature', categoryId: 'pre-qin', type: 'fact', tag: '文学常识', stem: '《论语》是由弟子及再传弟子编撰的{{0}}体散文集。', answers: ['语录'] },
  { id: 'lit-quyuan', source: 'literature', categoryId: 'pre-qin', type: 'fact', tag: '文学常识', stem: '屈原是我国古代伟大的{{0}}主义诗人，代表作《{{1}}》。', answers: ['浪漫', '离骚'] },
  { id: 'lit-shijing-name', source: 'literature', categoryId: 'pre-qin', type: 'fact', tag: '文学常识', stem: '《诗经》又被称为{{0}}。', answers: ['诗三百'] },
  { id: 'lit-quanxue-source', source: 'literature', categoryId: 'pre-qin', type: 'fact', tag: '文学常识', stem: '“青，取之于蓝，而青于蓝”出自《{{0}}》。', answers: ['劝学'] },
  { id: 'word-rou', source: 'classical', articleId: 'quanxue', type: 'word', tag: '重点字词', stem: '“輮以为轮”中，“輮”的意思是{{0}}。', answers: ['同“煣”，使木材弯曲'] },
  { id: 'word-ri', source: 'classical', articleId: 'quanxue', type: 'word', tag: '重点字词', stem: '“君子博学而日参省乎己”中，“日”的意思是{{0}}。', answers: ['每天'] },
  { id: 'word-de', source: 'classical', articleId: 'quanxue', type: 'word', tag: '重点字词', stem: '“积善成德，而神明自得”中，“得”的意思是{{0}}。', answers: ['获得'] },
  { id: 'word-qie', source: 'classical', articleId: 'quanxue', type: 'word', tag: '字词解释', stem: '“锲而不舍”中，“锲”的意思是{{0}}。', answers: ['雕刻'] },
  { id: 'translation-qing', source: 'classical', articleId: 'quanxue', type: 'translation', tag: '句子翻译', stem: '“青，取之于蓝，而青于蓝。”翻译为{{0}}。', answers: ['靛青从蓝草中提取，却比蓝草的颜色更青'] },
  { id: 'translation-jishan', source: 'classical', articleId: 'quanxue', type: 'translation', tag: '句子翻译', stem: '“积善成德，而神明自得。”翻译为{{0}}。', answers: ['积累善行养成良好的品德，自然会获得智慧'] },
  { id: 'translation-kuibu', source: 'classical', articleId: 'quanxue', type: 'translation', tag: '句子翻译', stem: '“故不积跬步，无以至千里。”翻译为{{0}}。', answers: ['所以不积累一步半步的路程，就没有办法到达千里之外'] },
  { id: 'translation-qie', source: 'classical', articleId: 'quanxue', type: 'translation', tag: '句子翻译', stem: '“锲而舍之，朽木不折；锲而不舍，金石可镂。”翻译为{{0}}。', answers: ['刻几下就停下，腐朽的木头也刻不断；不停地刻下去，金属和石头也能雕刻成功'] },
  { id: 'classical-shishuo-author', source: 'classical', articleId: 'shishuo', type: 'fact', tag: '文言文', stem: '《师说》的作者是{{0}}。', answers: ['韩愈'] }
];

module.exports = { config, categories, articles, questions };
