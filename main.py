import pandas as pd
import numpy as np
from torch.utils.data import DataLoader, TensorDataset
from common.text2id import X_data2id, get_answer_id
import os
import torch
from config.cfg import cfg, path
from common.load_data import load_data, tokenizer, data_split
from transformers import T5ForConditionalGeneration
import torch.optim as optim
from transformers import AdamW, get_linear_schedule_with_warmup
import torch.nn as nn
from torch.autograd import Variable
from common.metric import ScorePRF
from common.set_random_seed import setup_seed
import gc
import time

os.environ["TOKENIZERS_PARALLELISM"] = "true"
os.environ["CUDA_VISIBLE_DEVICES"] = str(cfg['gpu_id'])
device = torch.device(cfg['device'])

pos_X, pos_y = load_data(path['pos_path'])

acc_array = []
seeds = [10, 100, 1000, 2000, 4000]
average_acc = 0


for test_id in range(len(seeds)):
    print('~~~~~~~~~~~~~ 第', test_id+1, '次测试 ~~~~~~~~~~~~~~~~~~~')
    setup_seed(seeds[test_id])

    train_pos_X, train_pos_y, test_pos_X, test_pos_y = data_split(pos_X, pos_y, cfg['K'], cfg['Kt'])
    train_pos_X, test_pos_X = X_data2id(train_pos_X, tokenizer), X_data2id(test_pos_X, tokenizer)
    train_pos_y, test_pos_y = get_answer_id(train_pos_y, tokenizer), get_answer_id(test_pos_y, tokenizer)

    neg_X, neg_y = load_data(path['neg_path'])
    train_neg_X, train_neg_y, test_neg_X, test_neg_y = data_split(neg_X, neg_y, cfg['K'], cfg['Kt'])
    train_neg_X, test_neg_X = X_data2id(train_neg_X, tokenizer), X_data2id(test_neg_X, tokenizer)
    train_neg_y, test_neg_y = get_answer_id(train_neg_y, tokenizer), get_answer_id(test_neg_y, tokenizer)

    train_X = torch.tensor(np.vstack([train_pos_X, train_neg_X]))
    train_y = torch.tensor(np.vstack([train_pos_y, train_neg_y]))
    test_X = torch.tensor(np.vstack([test_pos_X, test_neg_X]))
    test_y = torch.tensor(np.vstack([test_pos_y, test_neg_y]))

    train_data = TensorDataset(train_X, train_y)
    test_data = TensorDataset(test_X, test_y)

    loader_train = DataLoader(
        dataset=train_data,
        batch_size=cfg['train_batch_size'],
        shuffle=True,
        num_workers=0,
        drop_last=False
    )

    loader_test = DataLoader(
        dataset=test_data,
        batch_size=cfg['test_batch_size'],
        shuffle=False,
        num_workers=0,
        drop_last=False
    )

    net = T5ForConditionalGeneration.from_pretrained(path['t5_path'])
    net = net.to(device)

    def change_lr(optimizer, new_lr):
        for param_group in optimizer.param_groups:
            param_group['lr'] = new_lr

    current_lr = cfg['learning_rate']
    if cfg['optimizer'] == 'Adam':
        optimizer = optim.Adam(net.parameters(), lr=cfg['learning_rate'])
    elif cfg['optimizer'] == 'SGD':
        optimizer = optim.SGD(net.parameters(), lr=cfg['learning_rate'], weight_decay=1e-3)
    elif cfg['optimizer'] == 'AdamW':
        # Prepare optimizer and schedule (linear warmup and decay)
        no_decay = ["bias", "LayerNorm.weight"]
        optimizer_grouped_parameters = [
            {
                "params": [p for n, p in net.named_parameters() if not any(nd in n for nd in no_decay)],
                "weight_decay": 0.0,
            },
            {
                "params": [p for n, p in net.named_parameters() if any(nd in n for nd in no_decay)],
                "weight_decay": 0.0,
            },
        ]
        optimizer = AdamW(optimizer_grouped_parameters, lr=cfg['learning_rate'], eps=1e-8)
        num_warmup_steps = 0
        scheduler = get_linear_schedule_with_warmup(optimizer, num_warmup_steps=num_warmup_steps,
                                                    num_training_steps=len(loader_train) // 1 * cfg['epoch'])
    # scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', factor=0.5, patience=4, verbose=True,
    #                                                        threshold=0.0001,
    #                                                        threshold_mode='rel', cooldown=0, min_lr=0, eps=1e-08)

    epoch = cfg['epoch']
    print(cfg)
    print(path)

    for i in range(epoch):
        # if i > 5:
        #     current_lr *= 0.95
        #     change_lr(optimizer, current_lr)

        print('-------------------------   training   ------------------------------')
        time0 = time.time()
        batch = 0
        ave_loss, sum_acc = 0, 0
        for batch_x, batch_y in loader_train:
            net.train()
            batch_x, batch_y = Variable(batch_x).long(), Variable(batch_y).long()
            batch_x, batch_y = batch_x.to(device), batch_y.to(device)

            mask1 = (batch_x != 0).type(torch.long)
            output = net(input_ids=batch_x, labels=batch_y, attention_mask=mask1)
            loss = output.loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
            optimizer.step()  # 更新权重

            if cfg['optimizer'] == 'AdamW':
                scheduler.step()
            optimizer.zero_grad()  # 清空梯度缓存
            ave_loss += loss
            batch += 1

            if batch % 2 == 0:
                print('epoch:{}/{},batch:{}/{},time:{}, loss:{},learning_rate:{}'.format(i + 1, epoch, batch,
                                                                                         len(loader_train),
                                                                                         round(time.time() - time0, 4),
                                                                                         loss,
                                                                                         optimizer.param_groups[
                                                                                             0]['lr']))
        # scheduler.step(ave_loss)
        print('------------------ epoch:{} ----------------'.format(i + 1))
        print('train_average_loss{}'.format(ave_loss / len(loader_train)))
        print('============================================'.format(i + 1))

        time0 = time.time()
        if (i + 1) % epoch == 0:
            label_out, label_y = [], []
            print('-------------------------   test   ------------------------------')
            sum_acc, num = 0, 0
            # torch.save(net.state_dict(), 'save_model/params' + str(i + 1) + '.pkl')
            for batch_x, batch_y in loader_test:
                net.eval()
                batch_x, batch_y = Variable(batch_x).long(), Variable(batch_y).long()
                batch_x, batch_y = batch_x.to(device), batch_y.to(device)

                with torch.no_grad():
                    mask1 = (batch_x != 0).type(torch.long)
                    output = net.generate(input_ids=batch_x, attention_mask=mask1)
                pred = output
                # _, pred = torch.max(output, dim=2)
                # pred = pred.cpu().detach().numpy()
                # batch_y = batch_y.cpu().detach().numpy()

                for j in range(pred.shape[0]):
                    label_out.append(tokenizer.decode(pred[j], skip_special_tokens=True))
                    label_y.append(tokenizer.decode(batch_y[j], skip_special_tokens=True))

            label_out = np.array(label_out)
            label_y = np.array(label_y)

            acc = (np.sum(label_y == label_out)) / len(label_y)
            print('------------------ epoch:{} ----------------'.format(i + 1))
            print('test_acc:{}, time:{}'.format(round(acc, 4), time.time()-time0))
            print('============================================'.format(i + 1))
            average_acc += acc * 100
            acc_array.append(acc * 100)

            with open('output/pre_out' + str(test_id) + '.txt', 'w', encoding='utf-8') as file:
                for j in range(len(label_out)):
                    file.write(str(label_out[j]))
                    file.write('\n')

    del label_y, label_out, test_X, test_y
    del loader_train, loader_test, train_X, train_y, train_neg_X, train_pos_X, test_pos_X, test_neg_X
    gc.collect()

average_acc /= 5
acc_array = np.array(acc_array)
print('average_acc:{}, std:{}'.format(round(average_acc, 4), round(np.std(acc_array, ddof=1), 4)))
