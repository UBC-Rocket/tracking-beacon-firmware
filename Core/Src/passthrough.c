#include "passthrough.h"
#include <string.h>

extern UART_HandleTypeDef huart1;
extern UART_HandleTypeDef huart2;

static PassthroughChannel radio_to_pc;
static PassthroughChannel pc_to_radio;

static void forward_data(PassthroughChannel *ch, uint16_t new_pos)
{
    uint16_t old_pos = ch->last_pos;
    if (new_pos == old_pos)
        return;

    if (ch->tx_busy)
        return;

    uint16_t len;
    if (new_pos > old_pos) {
        len = new_pos - old_pos;
        memcpy(ch->tx_buf, &ch->rx_buf[old_pos], len);
    } else {
        uint16_t first = PT_BUF_SIZE - old_pos;
        memcpy(ch->tx_buf, &ch->rx_buf[old_pos], first);
        memcpy(&ch->tx_buf[first], &ch->rx_buf[0], new_pos);
        len = first + new_pos;
    }

    ch->last_pos = new_pos;
    ch->tx_busy = 1;
    HAL_UART_Transmit_DMA(ch->tx_uart, ch->tx_buf, len);
}

void Passthrough_Init(void)
{
    radio_to_pc.rx_uart = &huart1;
    radio_to_pc.tx_uart = &huart2;
    radio_to_pc.last_pos = 0;
    radio_to_pc.tx_busy = 0;

    pc_to_radio.rx_uart = &huart2;
    pc_to_radio.tx_uart = &huart1;
    pc_to_radio.last_pos = 0;
    pc_to_radio.tx_busy = 0;

    HAL_UARTEx_ReceiveToIdle_DMA(&huart1, radio_to_pc.rx_buf, PT_BUF_SIZE);
    HAL_UARTEx_ReceiveToIdle_DMA(&huart2, pc_to_radio.rx_buf, PT_BUF_SIZE);
}

void Passthrough_HandleRxEvent(UART_HandleTypeDef *huart, uint16_t Size)
{
    if (huart->Instance == USART1) {
        forward_data(&radio_to_pc, Size % PT_BUF_SIZE);
    } else if (huart->Instance == USART2) {
        forward_data(&pc_to_radio, Size % PT_BUF_SIZE);
    }
}

void Passthrough_HandleTxCplt(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART2) {
        radio_to_pc.tx_busy = 0;
    } else if (huart->Instance == USART1) {
        pc_to_radio.tx_busy = 0;
    }
}

const uint8_t *Passthrough_GetCenterRxBuf(void)
{
    return radio_to_pc.rx_buf;
}

const uint8_t *Passthrough_GetPcRxBuf(void)
{
    return pc_to_radio.rx_buf;
}
