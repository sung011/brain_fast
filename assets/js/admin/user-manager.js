(function () {
    'use strict';

    const API_BASE = typeof baseUrl === 'string' ? baseUrl : '';
    let editModal = null;

    function reloadUserTables() {
        if (typeof window.jQuery === 'undefined') {
            return;
        }
        const $ = window.jQuery;
        $('.datatable-target').each(function () {
            if ($.fn.DataTable.isDataTable(this)) {
                $(this).DataTable().ajax.reload(null, false);
            }
        });
    }

    function bindSSE() {
        if (window.__userManagerSSEBound || !window.AdminUserSSE) {
            return;
        }
        window.__userManagerSSEBound = true;
        window.AdminUserSSE.on('user_created', reloadUserTables);
        window.AdminUserSSE.on('user_updated', reloadUserTables);
        window.AdminUserSSE.on('user_deleted', reloadUserTables);
    }

    async function requestJson(url, method, body) {
        const options = {
            method: method,
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
            },
        };

        if (body !== undefined) {
            options.headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(body);
        }

        try {
            const response = await fetch(API_BASE + url, options);
            const data = await response.json();

            if (!response.ok) {
                const detail = data.detail;
                const message = (typeof detail === 'object' && detail?.message)
                    || (typeof detail === 'string' ? detail : null)
                    || data.message
                    || '요청에 실패했습니다.';
                return {ok: false, message: message};
            }

            return data;
        } catch (error) {
            return {ok: false, message: '서버와의 통신에 실패했습니다.'};
        }
    }

    function mbLevelLabel(level) {
        return parseInt(level, 10) === 10 ? '관리자' : '일반 회원';
    }

    function getEditModal() {
        const modalElement = document.getElementById('userEditModal');
        if (!modalElement || typeof bootstrap === 'undefined') {
            return null;
        }
        if (!editModal) {
            editModal = bootstrap.Modal.getOrCreateInstance(modalElement);
        }
        return editModal;
    }

    function openEditModal(rowData) {
        const form = document.forms['userEdit'];
        const modal = getEditModal();
        if (!form || !modal) {
            return;
        }

        form.idx.value = rowData.idx;
        form.userId.value = rowData.user_id || '';
        form.userName.value = rowData.user_name || '';
        form.userBirth.value = rowData.user_birth || '';
        form.userUn.value = rowData.user_un || '';
        form.userSp.value = rowData.user_sp || '';
        form.mbLevel.value = String(rowData.mb_level || 1);
        form.userPw.value = '';
        form.userPwConfirm.value = '';
        modal.show();
    }

    async function saveEditForm() {
        const form = document.forms['userEdit'];
        const modal = getEditModal();
        if (!form) {
            return;
        }

        if (form.userName.value.trim() === '') {
            return utils.showToast('이름(닉네임)을 입력해주세요.', form.userName.focus());
        }
        if (form.userBirth.value === '') {
            return utils.showToast('생년월일을 선택해주세요.', form.userBirth.focus());
        }
        if (form.userUn.value.trim() === '') {
            return utils.showToast('학교를 입력해주세요.', form.userUn.focus());
        }
        if (form.userSp.value.trim() === '') {
            return utils.showToast('전공을 입력해주세요.', form.userSp.focus());
        }
        if (form.userPw.value !== '' && form.userPw.value.length < 4) {
            return utils.showToast('비밀번호는 4자 이상이어야 합니다.', form.userPw.focus());
        }
        if (form.userPw.value !== form.userPwConfirm.value) {
            return utils.showToast('비밀번호가 일치하지 않습니다.', form.userPwConfirm.focus());
        }

        const body = {
            user_name: form.userName.value.trim(),
            user_birth: form.userBirth.value,
            user_un: form.userUn.value.trim(),
            user_sp: form.userSp.value.trim(),
            mb_level: parseInt(form.mbLevel.value, 10),
        };
        if (form.userPw.value !== '') {
            body.user_pw = form.userPw.value;
        }

        const response = await requestJson('/admin/user/' + form.idx.value, 'PUT', body);
        if (response.ok) {
            if (modal) {
                modal.hide();
            }
            utils.showAlert('회원 정보가 수정되었습니다.');
            return;
        }

        utils.showError(response.message);
    }

    async function deleteUser(rowData) {
        const confirmResult = await utils.showConfirm('삭제 하시겠습니까?');
        if (!confirmResult.isConfirmed) {
            return;
        }

        const response = await requestJson('/admin/user/' + rowData.idx, 'DELETE');
        if (response.ok) {
            utils.showAlert('회원이 삭제되었습니다.');
            return;
        }

        utils.showError(response.message);
    }

    function bindTableActions() {
        if (typeof window.jQuery === 'undefined') {
            return;
        }
        const $ = window.jQuery;

        $(document).off('click.userManager', '.user-edit-btn').on('click.userManager', '.user-edit-btn', function (event) {
            event.preventDefault();
            const table = $(this).closest('table').DataTable();
            const rowData = table.row($(this).closest('tr')).data();
            if (rowData) {
                openEditModal(rowData);
            }
        });

        $(document).off('click.userManager', '.user-delete-btn').on('click.userManager', '.user-delete-btn', function (event) {
            event.preventDefault();
            const table = $(this).closest('table').DataTable();
            const rowData = table.row($(this).closest('tr')).data();
            if (rowData) {
                deleteUser(rowData);
            }
        });
    }

    function bindEditForm() {
        const saveBtn = document.getElementById('userEditSaveBtn');
        if (!saveBtn || saveBtn.dataset.bound === '1') {
            return;
        }
        saveBtn.dataset.bound = '1';
        saveBtn.addEventListener('click', saveEditForm);
    }

    function initDataTables() {
        if (typeof window.jQuery === 'undefined') {
            return;
        }
        const $ = window.jQuery;

        $('.datatable-target').each(function () {
            const $table = $(this);
            if ($.fn.DataTable.isDataTable($table)) {
                return;
            }

            $table.DataTable({
                destroy: true,
                ajax: {
                    url: '/admin/users_all',
                    type: 'GET',
                    dataSrc: '',
                },
                columns: [
                    {data: 'user_id'},
                    {data: 'user_name'},
                    {data: 'user_birth'},
                    {data: 'user_un'},
                    {data: 'user_sp'},
                    {
                        data: 'mb_level',
                        render: function (data) {
                            return mbLevelLabel(data);
                        },
                    },
                    {data: 'created_at'},
                    {
                        data: 'idx',
                        orderable: false,
                        defaultContent: '',
                        render: function (data, type, row) {
                            return '<button type="button" class="btn btn-datatable btn-icon btn-transparent-dark me-2 user-edit-btn" title="수정">' +
                                '<i data-feather="edit"></i></button>' +
                                '<button type="button" class="btn btn-datatable btn-icon btn-transparent-dark user-delete-btn" title="삭제">' +
                                '<i data-feather="trash-2"></i></button>';
                        },
                    },
                ],
                language: {
                    emptyTable: '등록된 회원이 없습니다.',
                    zeroRecords: '검색 결과가 없습니다.',
                    search: '검색:',
                    lengthMenu: '_MENU_개씩 보기',
                    info: '_START_ - _END_ / 총 _TOTAL_명',
                    infoEmpty: '0 명',
                    paginate: {previous: '이전', next: '다음'},
                },
                order: [[6, 'desc']],
                drawCallback: function () {
                    if (window.feather) {
                        feather.replace();
                    }
                },
            });
        });

        if (window.feather) {
            feather.replace();
        }
    }

    function init() {
        initDataTables();
        bindTableActions();
        bindEditForm();
        bindSSE();
    }

    document.addEventListener('DOMContentLoaded', init);
    if (document.readyState !== 'loading') {
        init();
    }
})();
