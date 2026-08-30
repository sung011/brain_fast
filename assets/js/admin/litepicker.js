// Litepicker
// 
// The date pickers in Material Admin Pro
// are powered by the Litepicker plugin.
// Litepicker is a lightweight, no dependencies
// date picker that allows for date ranges
// and other options. For more usage details
// visit the Litepicker docs.
// 
// Litepicker Documentation
// https://wakirin.github.io/Litepicker

window.addEventListener('DOMContentLoaded', event => {

    const litepickerSingleDate = document.getElementById('litepickerSingleDate');
    if (litepickerSingleDate) {
        new Litepicker({
            element: litepickerSingleDate,
            lang: 'ko-KR', // 달력 언어를 한국어로 설정
            format: 'YYYY년 MM월 DD일' // 날짜 표시 형식 변경
            //format: 'MMM DD, YYYY'
        });
    }

    const litepickerDateRange = document.getElementById('litepickerDateRange');
    if (litepickerDateRange) {
        new Litepicker({
            element: litepickerDateRange,
            singleMode: false,
            lang: 'ko-KR', // 달력 언어를 한국어로 설정
            format: 'YYYY년 MM월 DD일' // 날짜 표시 형식 변경
            //format: 'MMM DD, YYYY'
        });
    }

    const litepickerDateRange2Months = document.getElementById('litepickerDateRange2Months');
    if (litepickerDateRange2Months) {
        new Litepicker({
            element: litepickerDateRange2Months,
            singleMode: false,
            numberOfMonths: 2,
            numberOfColumns: 2,
            lang: 'ko-KR', // 달력 언어를 한국어로 설정
            format: 'YYYY년 MM월 DD일' // 날짜 표시 형식 변경
            //format: 'MMM DD, YYYY'
        });
    }

    const litepickerRangePlugin = document.getElementById('litepickerRangePlugin');
    if (litepickerRangePlugin && typeof Litepicker !== 'undefined') {
        const options = {
            element: litepickerRangePlugin,
            startDate: new Date(),
            endDate: new Date(),
            singleMode: false,
            numberOfMonths: 2,
            numberOfColumns: 2,
            lang: 'ko-KR',
            format: 'YYYY년 MM월 DD일',
        };
        try {
            new Litepicker({ ...options, plugins: ['ranges'] });
        } catch (err) {
            new Litepicker(options);
        }
    }
});
