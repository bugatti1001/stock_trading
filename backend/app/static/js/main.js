// Main JavaScript for Stock Analysis System

// Utility Functions
const utils = {
    // Format currency
    formatCurrency(value, decimals = 2) {
        if (value === null || value === undefined) return 'N/A';
        return `$${value.toFixed(decimals)}B`;
    },

    // Format percentage
    formatPercent(value, decimals = 2) {
        if (value === null || value === undefined) return 'N/A';
        return `${value.toFixed(decimals)}%`;
    },

    // Format date
    formatDate(dateString) {
        if (!dateString) return 'N/A';
        const date = new Date(dateString);
        return date.toLocaleDateString('zh-CN');
    },

    // Show loading
    showLoading(element) {
        element.classList.add('loading');
    },

    // Hide loading
    hideLoading(element) {
        element.classList.remove('loading');
    },

    // Show toast notification
    showToast(message, type = 'info') {
        // Simple alert for now, can be enhanced with Bootstrap toasts
        const alertClass = `alert-${type}`;
        const alertDiv = document.createElement('div');
        alertDiv.className = `alert ${alertClass} alert-dismissible fade show position-fixed top-0 end-0 m-3`;
        alertDiv.style.zIndex = '9999';
        alertDiv.innerHTML = `
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        document.body.appendChild(alertDiv);

        // Auto remove after 5 seconds
        setTimeout(() => {
            alertDiv.remove();
        }, 5000);
    }
};

// API Client
const api = {
    // Base URL
    baseUrl: '/api',

    // Generic GET request
    async get(endpoint) {
        try {
            const response = await fetch(`${this.baseUrl}${endpoint}`);
            return await response.json();
        } catch (error) {
            console.error('API Error:', error);
            throw error;
        }
    },

    // Generic POST request
    async post(endpoint, data) {
        try {
            const response = await fetch(`${this.baseUrl}${endpoint}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(data)
            });
            return await response.json();
        } catch (error) {
            console.error('API Error:', error);
            throw error;
        }
    },

    // Generic PUT request
    async put(endpoint, data) {
        try {
            const response = await fetch(`${this.baseUrl}${endpoint}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(data)
            });
            return await response.json();
        } catch (error) {
            console.error('API Error:', error);
            throw error;
        }
    },

    // Generic DELETE request
    async delete(endpoint) {
        try {
            const response = await fetch(`${this.baseUrl}${endpoint}`, {
                method: 'DELETE'
            });
            return await response.json();
        } catch (error) {
            console.error('API Error:', error);
            throw error;
        }
    },

    // Stock API
    stocks: {
        getAll() {
            return api.get('/stocks');
        },
        get(symbol) {
            return api.get(`/stocks/${symbol}`);
        },
        add(data) {
            return api.post('/stocks', data);
        },
        update(symbol, data) {
            return api.put(`/stocks/${symbol}`, data);
        },
        remove(symbol) {
            return api.delete(`/stocks/${symbol}`);
        },
        refresh(symbol) {
            return api.post(`/stocks/${symbol}/refresh`, {});
        },
        getFinancials(symbol) {
            return api.get(`/stocks/${symbol}/financials`);
        }
    },

    // Screening API
    screening: {
        getCriteria() {
            return api.get('/screening/criteria');
        },
        run(criteriaName) {
            return api.post('/screening/run', { criteria_name: criteriaName });
        }
    },

    // Data Collection API
    data: {
        fetchSEC(symbol, years = 3) {
            return api.post(`/data/sec/fetch/${symbol}?years=${years}`, {});
        },
        batchFetchSEC(symbols, years = 3) {
            return api.post('/data/sec/batch-fetch', { symbols, years });
        }
    },

    // News API
    news: {
        getAll(hours = 24) {
            return api.get(`/news?hours=${hours}`);
        },
        getDigest(hours = 24) {
            return api.get(`/news/digest?hours=${hours}`);
        }
    }
};

// Chart utilities
const charts = {
    // Default chart colors
    colors: [
        '#0d6efd', '#6610f2', '#6f42c1', '#d63384',
        '#dc3545', '#fd7e14', '#ffc107', '#20c997',
        '#0dcaf0', '#198754'
    ],

    // Create a pie chart
    createPieChart(ctx, labels, data, title = '') {
        return new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: data,
                    backgroundColor: this.colors
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    title: {
                        display: !!title,
                        text: title
                    },
                    legend: {
                        position: 'right'
                    }
                }
            }
        });
    },

    // Create a bar chart
    createBarChart(ctx, labels, data, title = '', label = 'Value') {
        return new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: label,
                    data: data,
                    backgroundColor: this.colors[0]
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    title: {
                        display: !!title,
                        text: title
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true
                    }
                }
            }
        });
    },

    // Create a line chart
    createLineChart(ctx, labels, datasets, title = '') {
        return new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: datasets.map((ds, i) => ({
                    label: ds.label,
                    data: ds.data,
                    borderColor: this.colors[i % this.colors.length],
                    backgroundColor: this.colors[i % this.colors.length] + '20',
                    tension: 0.4
                }))
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    title: {
                        display: !!title,
                        text: title
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true
                    }
                }
            }
        });
    }
};

// Initialize tooltips
document.addEventListener('DOMContentLoaded', function() {
    // Initialize Bootstrap tooltips
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Auto-hide alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert:not(.alert-permanent)');
    alerts.forEach(alert => {
        setTimeout(() => {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });
});

// Export for global use
window.utils = utils;
window.api = api;
window.charts = charts;
