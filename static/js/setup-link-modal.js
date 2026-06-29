(function (window, document) {
    function getJQuery() {
        return window.jQuery || window.$;
    }

    function notify(type, message) {
        var $ = getJQuery();
        if ($ && $.fn && $.fn.toast) {
            $('body').toast({class: type, message: message});
        }
    }

    function copyText(value, successMessage) {
        if (!value) return;
        if (navigator.clipboard) {
            navigator.clipboard.writeText(value).then(function () {
                notify('success', successMessage || 'Link copied.');
            });
            return;
        }
        var temp = document.createElement('input');
        document.body.appendChild(temp);
        temp.value = value;
        temp.select();
        document.execCommand('copy');
        temp.remove();
        notify('success', successMessage || 'Link copied.');
    }

    function getModal(target) {
        return target.closest('[data-setup-link-modal]');
    }

    function find(modal, selector) {
        return modal ? modal.querySelector(selector) : null;
    }

    function showElement(element) {
        if (element) element.style.display = '';
    }

    function setLink(modal, options) {
        var setupUrl = options.url || '';
        var expiresText = options.expiresAt ? 'Expires: ' + options.expiresAt : 'Expires in 48 hours. One use only.';
        if (options.maxUses) {
            expiresText += ' | Uses: ' + options.maxUses;
        }

        var input = find(modal, '[data-setup-link-input]');
        var meta = find(modal, '[data-setup-link-meta]');
        var open = find(modal, '[data-setup-link-open]');
        var whatsapp = find(modal, '[data-setup-link-whatsapp]');
        var result = find(modal, '[data-setup-link-result]');

        if (input) input.value = setupUrl;
        if (meta) meta.textContent = expiresText;
        if (open) open.setAttribute('href', setupUrl);
        if (whatsapp) {
            whatsapp.setAttribute(
                'href',
                'https://wa.me/?text=' + encodeURIComponent('Set your SchoolsStack password using this link: ' + setupUrl)
            );
        }
        showElement(result);
    }

    function show(modalSelector, options) {
        var modal = document.querySelector(modalSelector);
        if (!modal || !options || !options.url) return;
        setLink(modal, options);

        var $ = getJQuery();
        if ($ && $.fn && $.fn.modal) {
            $(modal).modal({autofocus: false}).modal('show');
        } else {
            modal.classList.add('visible', 'active');
            modal.style.display = 'block';
        }
    }

    function bindGenerate(config) {
        var $ = getJQuery();
        if (!$ || !$.ajax) return;

        var $modal = $(config.modalSelector);
        var $openButton = $(config.openSelector);
        var $createButton = $modal.find('[data-setup-link-create]');

        if ($.fn && $.fn.dropdown) {
            $modal.find('[data-setup-link-expiry]').dropdown();
        }

        $openButton.on('click', function () {
            $modal.find('[data-setup-link-result]').hide();
            $modal.modal({autofocus: false}).modal('show');
        });

        $createButton.on('click', function () {
            $createButton.addClass('loading disabled');
            $.ajax({
                type: 'POST',
                url: config.url,
                data: {
                    expires_hours: $modal.find('[data-setup-link-expiry]').val(),
                    max_uses: $modal.find('[data-setup-link-max-uses]').val()
                },
                success: function (response) {
                    if (response.success) {
                        setLink($modal[0], {
                            url: response.url,
                            expiresAt: response.expiresAt,
                            maxUses: response.maxUses
                        });
                        notify('success', config.successMessage || 'Password setup link generated.');
                    } else {
                        notify('warning', response.message || 'Unable to generate link.');
                    }
                },
                error: function (xhr) {
                    var message = xhr.responseJSON && xhr.responseJSON.message ? xhr.responseJSON.message : 'Unable to generate link.';
                    notify('error', message);
                },
                complete: function () {
                    $createButton.removeClass('loading disabled');
                }
            });
        });
    }

    document.addEventListener('click', function (event) {
        var copyButton = event.target.closest('[data-setup-link-copy]');
        if (copyButton) {
            var copyInput = find(getModal(copyButton), '[data-setup-link-input]');
            copyText(copyInput ? copyInput.value : '', 'Setup link copied.');
            return;
        }

        var shareButton = event.target.closest('[data-setup-link-share]');
        if (shareButton) {
            var shareInput = find(getModal(shareButton), '[data-setup-link-input]');
            var value = shareInput ? shareInput.value : '';
            if (!value) return;
            if (navigator.share) {
                navigator.share({
                    title: 'SchoolsStack password setup',
                    text: 'Set your SchoolsStack password.',
                    url: value
                });
            } else {
                copyText(value, 'Setup link copied for sharing.');
            }
        }
    });

    window.SetupLinkModal = {
        bindGenerate: bindGenerate,
        show: show
    };
})(window, document);
